"""
Cascadeur Real-Time Animation Streamer
======================================
Streams live character animation from Cascadeur to Blender in real time via UDP.

Uses PySide6 QTimer on Cascadeur's Main GUI Thread:
- Runs directly inside Cascadeur's Qt event loop with zero thread mutex contention.
- Streams live playback at full 60 FPS in real time.
- Streams interactive scrubbing immediately on timeline drag.
- Throttles to ~10 FPS heartbeat when paused to keep Blender connected without spamming.
- Fast joint caching (< 0.5ms per frame) ensures zero viewport lag.

Usage in Cascadeur:
  import cascadeur_streamer
  cascadeur_streamer.stop_all()
  import importlib; importlib.reload(cascadeur_streamer)
  cascadeur_streamer.streamer.start()
"""

import gc
import json
import math
import socket
import sys
import threading
import time

# Try importing PySide6 (Cascadeur's native Qt framework)
try:
    from PySide6 import QtCore
    HAS_QT = True
except ImportError:
    try:
        from PyQt6 import QtCore
        HAS_QT = True
    except ImportError:
        QtCore = None
        HAS_QT = False

# Try importing Cascadeur's native C++ Python API
try:
    import csc
    HAS_CASCADEUR = True
except ImportError:
    csc = None
    HAS_CASCADEUR = False

# Cleanup any previous streamer instances or timers in this session
import builtins
if hasattr(builtins, "_cascadeur_streamer_qtimer") and builtins._cascadeur_streamer_qtimer is not None:
    try:
        builtins._cascadeur_streamer_qtimer.stop()
        builtins._cascadeur_streamer_qtimer = None
    except Exception:
        pass

if hasattr(builtins, "_cascadeur_pose_receiver_qtimer") and builtins._cascadeur_pose_receiver_qtimer is not None:
    try:
        builtins._cascadeur_pose_receiver_qtimer.stop()
        builtins._cascadeur_pose_receiver_qtimer = None
    except Exception:
        pass

for _obj in gc.get_objects():
    if type(_obj).__name__ in ("CascadeurStreamer", "CascadeurPoseReceiver"):
        try:
            _obj.stop()
        except Exception:
            _obj.is_running = False


class SafeJSONEncoder(json.JSONEncoder):
    """Ensures numpy types and custom types serialize to standard JSON without error."""
    def default(self, obj):
        if hasattr(obj, "item"):
            return obj.item()
        if hasattr(obj, "tolist"):
            return obj.tolist()
        if isinstance(obj, (set, tuple)):
            return list(obj)
        try:
            return float(obj)
        except Exception:
            pass
        return str(obj)


def extract_quat(raw_r):
    """Safely extract [w, x, y, z] from Cascadeur Quaternion, Rotation, or array."""
    if raw_r is None:
        return [1.0, 0.0, 0.0, 0.0]

    if hasattr(raw_r, "to_quaternion"):
        try:
            raw_r = raw_r.to_quaternion()
        except Exception:
            pass

    try:
        if hasattr(raw_r, "w"):
            w = raw_r.w() if callable(raw_r.w) else raw_r.w
            x = raw_r.x() if callable(raw_r.x) else raw_r.x
            y = raw_r.y() if callable(raw_r.y) else raw_r.y
            z = raw_r.z() if callable(raw_r.z) else raw_r.z
            return [float(w), float(x), float(y), float(z)]
    except Exception:
        pass

    try:
        flat = list(raw_r.flatten()) if hasattr(raw_r, "flatten") else list(raw_r)
        if len(flat) == 4:
            return [float(flat[0]), float(flat[1]), float(flat[2]), float(flat[3])]
    except Exception:
        pass

    return [1.0, 0.0, 0.0, 0.0]


def extract_pos(raw_p):
    """Safely extract [x, y, z] from Cascadeur position vector or array."""
    if raw_p is None:
        return [0.0, 0.0, 0.0]

    try:
        flat = list(raw_p.flatten()) if hasattr(raw_p, "flatten") else list(raw_p)
        if len(flat) >= 3:
            return [float(flat[0]), float(flat[1]), float(flat[2])]
    except Exception:
        pass

    try:
        if hasattr(raw_p, "x"):
            x = raw_p.x() if callable(raw_p.x) else raw_p.x
            y = raw_p.y() if callable(raw_p.y) else raw_p.y
            z = raw_p.z() if callable(raw_p.z) else raw_p.z
            return [float(x), float(y), float(z)]
    except Exception:
        pass

    return [0.0, 0.0, 0.0]


def mat4_to_pos_quat(m):
    """Decompose a 4x4 matrix into position [x, y, z] and quaternion [w, x, y, z]."""
    try:
        if hasattr(m, "tolist"):
            m = m.tolist()
        pos = [float(m[0][3]), float(m[1][3]), float(m[2][3])]
        r00, r01, r02 = float(m[0][0]), float(m[0][1]), float(m[0][2])
        r10, r11, r12 = float(m[1][0]), float(m[1][1]), float(m[1][2])
        r20, r21, r22 = float(m[2][0]), float(m[2][1]), float(m[2][2])

        l0 = math.sqrt(r00*r00 + r10*r10 + r20*r20) or 1.0
        l1 = math.sqrt(r01*r01 + r11*r11 + r21*r21) or 1.0
        l2 = math.sqrt(r02*r02 + r12*r12 + r22*r22) or 1.0
        r00, r10, r20 = r00/l0, r10/l0, r20/l0
        r01, r11, r21 = r01/l1, r11/l1, r21/l1
        r02, r12, r22 = r02/l2, r12/l2, r22/l2

        trace = r00 + r11 + r22
        if trace > 0.0:
            s = 0.5 / math.sqrt(trace + 1.0)
            w = 0.25 / s
            x = (r21 - r12) * s
            y = (r02 - r20) * s
            z = (r10 - r01) * s
        elif r00 > r11 and r00 > r22:
            s = 2.0 * math.sqrt(1.0 + r00 - r11 - r22)
            w = (r21 - r12) / s
            x = 0.25 * s
            y = (r01 + r10) / s
            z = (r02 + r20) / s
        elif r11 > r22:
            s = 2.0 * math.sqrt(1.0 + r11 - r00 - r22)
            w = (r02 - r20) / s
            x = (r01 + r10) / s
            y = 0.25 * s
            z = (r12 + r21) / s
        else:
            s = 2.0 * math.sqrt(1.0 + r22 - r00 - r11)
            w = (r10 - r01) / s
            x = (r02 + r20) / s
            y = (r12 + r21) / s
            z = 0.25 * s
        return pos, [float(w), float(x), float(y), float(z)]
    except Exception:
        return [0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]


def safe_get_data_value(dw, data_id, frame_idx=None):
    """Read data value safely without throwing CscExceptions."""
    if data_id is None:
        return None
    if hasattr(data_id, "is_null") and data_id.is_null():
        return None
    try:
        if frame_idx is not None:
            return dw.get_data_value(data_id, int(frame_idx))
        return dw.get_data_value(data_id)
    except Exception:
        return None


def get_active_cascadeur_scene():
    """Retrieve the active csc.domain.Scene from Cascadeur."""
    # Prefer the native API: avoid creating Python wrappers for each streamed value.
    try:
        return csc.app.get_application().get_scene_manager().current_scene().domain_scene()
    except Exception:
        pass
    # Method 1: pycsc wrapper
    try:
        import pycsc
        view_scene = pycsc.get_current_scene()
        if hasattr(view_scene, "ds") and view_scene.ds is not None:
            return view_scene.ds
        if hasattr(view_scene, "domain_scene") and view_scene.domain_scene() is not None:
            return view_scene.domain_scene()
    except Exception:
        pass

    # Method 2: csc.app get_application().current_scene()
    try:
        app = csc.app.get_application()
        if hasattr(app, "current_scene"):
            v_scene = app.current_scene()
            if hasattr(v_scene, "domain_scene"):
                return v_scene.domain_scene()
            return v_scene
    except Exception:
        pass

    # Method 3: scene_manager
    try:
        app = csc.app.get_application()
        sm = app.get_scene_manager()
        v_scene = sm.current_scene()
        if hasattr(v_scene, "domain_scene"):
            return v_scene.domain_scene()
        return v_scene
    except Exception:
        pass

    return None


class CascadeurStreamer:
    def __init__(self, host="127.0.0.1", port=9999, fps=60):
        self.host = host
        self.port = port
        self.fps = fps
        self.interval = 1.0 / max(1, fps)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.is_running = False
        self.packets_sent = 0

        self._qtimer = None
        self._thread = None

        self._joint_map = []       # Pre-resolved [(joint_name, rot_id, pos_id)]
        self._last_scene_id = None
        self._last_sent_frame = -999
        self._last_sent_time = 0.0
        self._last_valid_frame = 0
        self._burst_count = 0
        self.last_error = ""

    def _build_joint_map(self, scene):
        """Pre-resolve joint IDs and validate data IDs once so per-frame streaming takes < 0.2ms with 0 exceptions."""
        joint_map = []
        try:
            mv = scene.model_viewer() if hasattr(scene, "model_viewer") else None
            if not mv:
                return []
            bw = mv.behaviour_viewer()
            dw = mv.data_viewer()
            joint_ids = bw.get_behaviours("Joint")
            for j_id in joint_ids:
                try:
                    owner_id = bw.get_behaviour_owner(j_id)
                    joint_name = mv.get_object_name(owner_id) if hasattr(mv, "get_object_name") else mv.object_name(owner_id)

                    rot_id = None
                    pos_id = None
                    g_rot_id = None
                    g_pos_id = None
                    gm_id = None

                    tr_id = bw.get_behaviour_by_name(owner_id, "Transform")
                    if not tr_id.is_null():
                        r_id = bw.get_behaviour_data(tr_id, "local_rotation")
                        if not r_id.is_null():
                            rot_id = r_id

                        p_id = bw.get_behaviour_data(tr_id, "local_position")
                        if not p_id.is_null():
                            pos_id = p_id

                        gr_id = bw.get_behaviour_data(tr_id, "global_rotation")
                        if not gr_id.is_null():
                            g_rot_id = gr_id

                        gp_id = bw.get_behaviour_data(tr_id, "global_position")
                        if not gp_id.is_null():
                            g_pos_id = gp_id

                    # Global matrix is on Joint behaviour j_id (4x4 matrix with world pos & rot)
                    gm = bw.get_behaviour_data(j_id, "global_matrix")
                    if not gm.is_null():
                        gm_id = gm

                    joint_map.append((joint_name, rot_id, pos_id, g_pos_id, g_rot_id, gm_id))
                except Exception:
                    continue
        except Exception as e:
            self.last_error = f"Joint map error: {e}"
        return joint_map

    def get_cascadeur_joint_data(self, scene=None, frame_num=None):
        """
        Extract joints and transforms (both local and true global) from the active Cascadeur scene.
        Runs blazing fast (< 0.2ms) with zero CscExceptions.
        """
        if scene is None:
            if not HAS_CASCADEUR or csc is None:
                return self._generate_simulated_pose(frame=frame_num if frame_num is not None else 0)
            scene = get_active_cascadeur_scene()

        if not scene:
            self.last_error = "No active Cascadeur scene found."
            return {
                "version": "1.0",
                "frame": 0,
                "fps": self.fps,
                "seq": self.packets_sent,
                "time": time.time(),
                "bones": {},
            }

        # 1. Read playhead frame directly from scene if not specified
        if frame_num is None:
            try:
                frame_num = int(scene.get_current_frame())
                self._last_valid_frame = frame_num
            except Exception:
                try:
                    frame_num = int(scene.current_frame())
                    self._last_valid_frame = frame_num
                except Exception:
                    frame_num = getattr(self, "_last_valid_frame", 0)

        # 2. Build joint map once on start (cached, never rebuilt per frame)
        if not self._joint_map:
            self._joint_map = self._build_joint_map(scene)

        # 3. Extract transforms for frame_num
        joints_dict = {}
        try:
            mv = scene.model_viewer()
            dw = mv.data_viewer()

            bind_invs = _cached_casc_bind_inv_quats
            if HAS_CASCADEUR and csc is not None:
                try:
                    _ensure_cascadeur_globals(scene)
                    bind_invs = _cached_casc_bind_inv_quats
                except Exception:
                    bind_invs = None

            for joint_name, rot_id, pos_id, g_pos_id, g_rot_id, gm_id in self._joint_map:
                try:
                    pos = [0.0, 0.0, 0.0]
                    rot = [1.0, 0.0, 0.0, 0.0]
                    g_pos = None
                    g_rot = None

                    # Local rotation & position
                    if rot_id is not None:
                        raw_rot = safe_get_data_value(dw, rot_id, frame_num)
                        if raw_rot is not None:
                            rot = extract_quat(raw_rot)

                    if pos_id is not None:
                        raw_pos = safe_get_data_value(dw, pos_id, frame_num)
                        if raw_pos is not None:
                            pos = extract_pos(raw_pos)

                    # Native global rotation & position from Cascadeur's Transform behaviour
                    if g_pos_id is not None:
                        raw_gp = safe_get_data_value(dw, g_pos_id, frame_num)
                        if raw_gp is not None:
                            g_pos = extract_pos(raw_gp)

                    if g_rot_id is not None:
                        raw_gr = safe_get_data_value(dw, g_rot_id, frame_num)
                        if raw_gr is not None:
                            g_rot = extract_quat(raw_gr)

                    # Fallback to global_matrix if native global rot/pos data is missing
                    if gm_id is not None and (g_pos is None or g_rot is None):
                        raw_mat = safe_get_data_value(dw, gm_id, frame_num)
                        if raw_mat is not None:
                            m_pos, m_rot = mat4_to_pos_quat(raw_mat)
                            if g_pos is None:
                                g_pos = m_pos
                            if g_rot is None:
                                g_rot = m_rot

                    if g_pos is None:
                        g_pos = pos
                    if g_rot is None:
                        g_rot = rot

                    delta_rot = None
                    if g_rot and bind_invs and joint_name in bind_invs:
                        try:
                            w1, x1, y1, z1 = g_rot
                            w2, x2, y2, z2 = bind_invs[joint_name]
                            delta_rot = [
                                w1*w2 - x1*x2 - y1*y2 - z1*z2,
                                w1*x2 + x1*w2 + y1*z2 - z1*y2,
                                w1*y2 - x1*z2 + y1*w2 + z1*x2,
                                w1*z2 + x1*y2 - y1*x2 + z1*w2,
                            ]
                        except Exception:
                            delta_rot = None

                    joint_entry = {
                        "pos": pos,
                        "rot": rot,
                        "g_pos": g_pos,
                        "g_rot": g_rot,
                    }
                    if delta_rot is not None:
                        joint_entry["delta_rot"] = delta_rot

                    joints_dict[joint_name] = joint_entry
                except Exception:
                    continue

        except Exception as e:
            self.last_error = f"Extraction error: {e}"

        return {
            "version": "1.0",
            "frame": frame_num,
            "fps": self.fps,
            "seq": self.packets_sent,
            "time": time.time(),
            "bones": joints_dict,
        }

    def _generate_simulated_pose(self, frame=0):
        """Generate a procedural walk cycle for testing outside Cascadeur."""
        t = time.time() * 2.5
        leg_angle = math.sin(t) * 0.4
        arm_angle = -math.sin(t) * 0.4
        hip_bounce = abs(math.sin(t * 2)) * 0.05

        def quat_x(angle):
            half = angle * 0.5
            return [math.cos(half), math.sin(half), 0.0, 0.0]

        def quat_z(angle):
            half = angle * 0.5
            return [math.cos(half), 0.0, 0.0, math.sin(half)]

        sim_frame = int((time.time() * self.fps) % 120)

        # Compute global positions in Cascadeur Y-Up world space
        hips_gp = [0.0, 1.0 + hip_bounce, 0.0]
        spine_gp = [0.0, 1.2 + hip_bounce, 0.0]
        spine1_gp = [0.0, 1.35 + hip_bounce, 0.0]
        neck_gp = [0.0, 1.5 + hip_bounce, 0.0]
        head_gp = [0.0, 1.65 + hip_bounce, 0.0]
        l_arm_gp = [0.2, 1.45 + hip_bounce, 0.0]
        r_arm_gp = [-0.2, 1.45 + hip_bounce, 0.0]
        l_leg_gp = [0.1, 0.9 + hip_bounce, 0.0]
        r_leg_gp = [-0.1, 0.9 + hip_bounce, 0.0]
        l_knee_gp = [0.1, 0.5 + hip_bounce, math.sin(leg_angle) * 0.2]
        r_knee_gp = [-0.1, 0.5 + hip_bounce, -math.sin(leg_angle) * 0.2]

        return {
            "version": "1.0",
            "frame": sim_frame,
            "fps": self.fps,
            "seq": self.packets_sent,
            "time": time.time(),
            "bones": {
                "Hips": {"pos": hips_gp, "rot": [1.0, 0.0, 0.0, 0.0], "g_pos": hips_gp, "g_rot": [1.0, 0.0, 0.0, 0.0]},
                "Spine": {"pos": [0.0, 0.2, 0.0], "rot": quat_x(math.sin(t) * 0.05), "g_pos": spine_gp, "g_rot": quat_x(math.sin(t) * 0.05)},
                "Spine1": {"pos": [0.0, 0.15, 0.0], "rot": quat_x(math.sin(t) * 0.05), "g_pos": spine1_gp, "g_rot": quat_x(math.sin(t) * 0.05)},
                "Neck": {"pos": [0.0, 0.15, 0.0], "rot": [1.0, 0.0, 0.0, 0.0], "g_pos": neck_gp, "g_rot": [1.0, 0.0, 0.0, 0.0]},
                "Head": {"pos": [0.0, 0.15, 0.0], "rot": quat_z(math.sin(t) * 0.1), "g_pos": head_gp, "g_rot": quat_z(math.sin(t) * 0.1)},
                "LeftArm": {"pos": [0.2, 0.0, 0.0], "rot": quat_x(arm_angle), "g_pos": l_arm_gp, "g_rot": quat_x(arm_angle)},
                "RightArm": {"pos": [-0.2, 0.0, 0.0], "rot": quat_x(-arm_angle), "g_pos": r_arm_gp, "g_rot": quat_x(-arm_angle)},
                "LeftUpLeg": {"pos": [0.1, -0.1, 0.0], "rot": quat_x(leg_angle), "g_pos": l_leg_gp, "g_rot": quat_x(leg_angle)},
                "RightUpLeg": {"pos": [-0.1, -0.1, 0.0], "rot": quat_x(-leg_angle), "g_pos": r_leg_gp, "g_rot": quat_x(-leg_angle)},
                "LeftLeg": {"pos": [0.0, -0.4, 0.0], "rot": quat_x(max(0.0, -leg_angle * 1.2)), "g_pos": l_knee_gp, "g_rot": [1.0, 0.0, 0.0, 0.0]},
                "RightLeg": {"pos": [0.0, -0.4, 0.0], "rot": quat_x(max(0.0, leg_angle * 1.2)), "g_pos": r_knee_gp, "g_rot": [1.0, 0.0, 0.0, 0.0]},
            },
        }

    def _timer_tick(self):
        """
        Called by QTimer on the Main GUI Thread at 60 FPS.
        Directly queries Cascadeur's active frame and broadcasts live data.
        """
        if not self.is_running:
            return

        try:
            if not HAS_CASCADEUR or csc is None:
                data = self._generate_simulated_pose()
                payload = json.dumps(data, cls=SafeJSONEncoder).encode("utf-8")
                self.sock.sendto(payload, (self.host, self.port))
                self.packets_sent += 1
                return

            scene = get_active_cascadeur_scene()
            if not scene:
                return

            try:
                frame_num = int(scene.get_current_frame())
            except Exception:
                frame_num = self._last_valid_frame

            now = time.time()

            # When timeline jumps or scrubs discontinuously (> 1 frame difference), send a burst of 5 packets
            # to guarantee instant delivery and zero catch-up lag before reverting to the 10 FPS heartbeat.
            # During normal sequential playback (frame advances by 1), stream smoothly 1 packet per frame.
            if abs(frame_num - self._last_sent_frame) > 1:
                self._burst_count = 5

            if self._burst_count > 0:
                self._burst_count -= 1
            elif frame_num == self._last_sent_frame and (now - self._last_sent_time) < self.interval:
                return

            data = self.get_cascadeur_joint_data(scene=scene)
            if not data or not data.get("bones"):
                return

            import live_sync
            try:
                live_sync.publish(self, scene, data)
            except Exception as error:
                data['key_sync_error'] = str(error)
                self.last_error = f'Key sync: {error}'

            payload = json.dumps(data, cls=SafeJSONEncoder).encode("utf-8")
            if len(payload) > 60000:
                live_sync.send_message(self.sock, self.host, self.port, data)
            else:
                self.sock.sendto(payload, (self.host, self.port))
            self.packets_sent += 1
            self._last_sent_frame = frame_num
            self._last_sent_time = now
            self._last_valid_frame = frame_num

        except Exception as e:
            self.last_error = f"Timer tick error: {e}"

    def _stream_loop(self):
        """Fallback streaming loop for standalone test mode outside Cascadeur."""
        while self.is_running:
            start_time = time.time()
            try:
                data = self.get_cascadeur_joint_data()
                payload = json.dumps(data, cls=SafeJSONEncoder).encode("utf-8")
                self.sock.sendto(payload, (self.host, self.port))
                self.packets_sent += 1
            except Exception as e:
                self.last_error = f"Loop error: {e}"

            elapsed = time.time() - start_time
            sleep_time = self.interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def start(self):
        """Start streaming."""
        if self.is_running:
            msg = f"⚠ Cascadeur Streamer is ALREADY RUNNING! (Packets sent: {self.packets_sent})"
            print(msg)
            sys.stdout.flush()
            return msg

        self.is_running = True
        self._last_sent_frame = -999
        self._last_sent_time = 0.0
        self._joint_map = []

        # Automatically start pose receiver for bidirectional link (Blender -> Cascadeur)
        global pose_receiver
        if pose_receiver and not pose_receiver.is_running:
            try:
                pose_receiver.start()
            except Exception:
                pass

        # Mode A: Native Qt QTimer on Main GUI Thread (preferred in Cascadeur)
        if HAS_QT and HAS_CASCADEUR:
            interval_ms = max(1, int(1000.0 / self.fps))
            self._qtimer = QtCore.QTimer()
            self._qtimer.setInterval(interval_ms)
            self._qtimer.timeout.connect(self._timer_tick)
            self._qtimer.start()

            # Keep global reference to prevent PySide GC
            import builtins
            builtins._cascadeur_streamer_qtimer = self._qtimer

            msg = f"✓ Cascadeur Streamer STARTED (Qt Main-Thread Timer)! Streaming to {self.host}:{self.port} at {self.fps} FPS."
            print(msg)
            sys.stdout.flush()
            return msg

        # Mode B: Background OS Thread (standalone test outside Cascadeur)
        self._thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._thread.start()
        msg = f"✓ Cascadeur Streamer STARTED (Thread mode)! Streaming to {self.host}:{self.port} at {self.fps} FPS."
        print(msg)
        sys.stdout.flush()
        return msg

    def stop(self):
        """Stop streaming."""
        if not self.is_running:
            msg = "Cascadeur Streamer is NOT running."
            print(msg)
            sys.stdout.flush()
            return msg

        self.is_running = False

        if self._qtimer is not None:
            try:
                self._qtimer.stop()
                self._qtimer.timeout.disconnect(self._timer_tick)
            except Exception:
                pass
            self._qtimer = None
            import builtins
            builtins._cascadeur_streamer_qtimer = None

        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None

        # Stop pose receiver
        global pose_receiver
        if pose_receiver and pose_receiver.is_running:
            try:
                pose_receiver.stop()
            except Exception:
                pass

        msg = f"✓ Cascadeur Streamer STOPPED. (Total packets sent: {self.packets_sent})"
        print(msg)
        sys.stdout.flush()
        return msg


# --- Joint Name Matching & Pose Application ---

def clean_bone_name(n):
    if not n:
        return ""
    clean = n.split(":")[-1].split("|")[-1].lower()
    for prefix in ("mixamorig:", "mixamorig_", "mixamorig", "cc_base_", "cc_"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    return clean


def is_helper_bone(bname):
    """Identifies subsidiary, twist, share, or non-skeletal helper bones."""
    if not bname:
        return False
    b_low = bname.lower()
    if "neck" in b_low:
        return False
    return any(skip in b_low for skip in ("twist", "share", "breast", "rib", "boneroot", "rootjoint", "jawroot", "jaw", "facial", "tongue", "teeth", "eye"))


def normalize_name(n):
    """Normalize joint/bone name for resilient matching."""
    if not n:
        return ""
    clean = clean_bone_name(n)
    return clean.strip().replace("_", "").replace(" ", "").replace(".", "")


BONE_SYNONYMS = {
    "hips": ["pelvis", "root", "hip", "b_hips", "root_joint", "cc_base_hip"],
    "pelvis": ["hips", "root", "hip", "b_pelvis", "cc_base_hip", "cc_base_pelvis"],
    "stomach": ["spine", "spine_01", "spine1", "b_spine", "spine_fk", "lowerspine", "spine01"],
    "spine": ["stomach", "spine_01", "spine1", "b_spine", "spine_fk", "spine01"],
    "spine1": ["stomach", "spine_02", "spine2", "b_spine1", "spine02"],
    "spine2": ["chest", "spine_03", "upperchest", "b_spine2"],
    "chest": ["spine2", "spine_02", "spine_03", "upperchest"],
    "neck": ["neck_01", "b_neck", "neck01", "cc_base_necktwist01", "necktwist01", "neck_twist_01"],
    "neck1": ["neck_02", "neck02", "cc_base_necktwist02", "necktwist02", "neck_twist_02"],
    "head": ["head_01", "b_head", "head01"],
    # Left Arm
    "leftshoulder": ["clavicle_l", "shoulder_l", "l_shoulder", "l_clavicle", "b_leftshoulder", "l_clavicle"],
    "leftarm": ["arm_l", "left_arm", "upperarm_l", "b_leftarm", "l_upperarm"],
    "leftforearm": ["forearm_l", "lowerarm_l", "b_leftforearm", "l_lowerarm", "left_forearm", "l_forearm"],
    "lefthand": ["hand_l", "b_lefthand", "l_hand", "left_hand"],
    # Right Arm
    "rightshoulder": ["clavicle_r", "shoulder_r", "r_shoulder", "r_clavicle", "b_rightshoulder", "r_clavicle"],
    "rightarm": ["arm_r", "right_arm", "upperarm_r", "b_rightarm", "r_upperarm"],
    "rightforearm": ["forearm_r", "lowerarm_r", "b_rightforearm", "r_lowerarm", "right_forearm", "r_forearm"],
    "righthand": ["hand_r", "b_righthand", "r_hand", "right_hand"],
    # Left Leg
    "leftupleg": ["thigh_l", "upleg_l", "b_leftupleg", "l_thigh", "leftthigh", "left_leg"],
    "leftleg": ["calf_l", "shin_l", "b_leftleg", "l_calf", "leftshin"],
    "leftfoot": ["foot_l", "b_leftfoot", "l_foot", "left_foot"],
    "lefttoebase": ["toe_l", "b_lefttoe", "l_toe", "left_toe", "l_toebase"],
    # Right Leg
    "rightupleg": ["thigh_r", "upleg_r", "b_rightupleg", "r_thigh", "rightthigh", "right_leg"],
    "rightleg": ["calf_r", "shin_r", "b_rightleg", "r_calf", "rightshin"],
    "rightfoot": ["foot_r", "b_rightfoot", "r_foot", "right_foot"],
    "righttoebase": ["toe_r", "b_righttoe", "r_toe", "right_toe", "r_toebase"],
    # Left Fingers
    "leftthumb1": ["thumb_01_l", "l_thumb1", "lefthandthumb1", "cc_base_l_thumb1"],
    "leftthumb2": ["thumb_02_l", "l_thumb2", "lefthandthumb2", "cc_base_l_thumb2"],
    "leftthumb3": ["thumb_03_l", "l_thumb3", "lefthandthumb3", "cc_base_l_thumb3"],
    "leftindex1": ["index_01_l", "l_index1", "lefthandindex1", "cc_base_l_index1"],
    "leftindex2": ["index_02_l", "l_index2", "lefthandindex2", "cc_base_l_index2"],
    "leftindex3": ["index_03_l", "l_index3", "lefthandindex3", "cc_base_l_index3"],
    "leftmiddle1": ["middle_01_l", "l_mid1", "l_middle1", "lefthandmiddle1", "cc_base_l_mid1"],
    "leftmiddle2": ["middle_02_l", "l_mid2", "l_middle2", "lefthandmiddle2", "cc_base_l_mid2"],
    "leftmiddle3": ["middle_03_l", "l_mid3", "l_middle3", "lefthandmiddle3", "cc_base_l_mid3"],
    "leftring1": ["ring_01_l", "l_ring1", "lefthandring1", "cc_base_l_ring1"],
    "leftring2": ["ring_02_l", "l_ring2", "lefthandring2", "cc_base_l_ring2"],
    "leftring3": ["ring_03_l", "l_ring3", "lefthandring3", "cc_base_l_ring3"],
    "leftpinky1": ["pinky_01_l", "l_pinky1", "little_01_l", "lefthandpinky1", "cc_base_l_pinky1"],
    "leftpinky2": ["pinky_02_l", "l_pinky2", "little_02_l", "lefthandpinky2", "cc_base_l_pinky2"],
    "leftpinky3": ["pinky_03_l", "l_pinky3", "little_03_l", "lefthandpinky3", "cc_base_l_pinky3"],
    # Right Fingers
    "rightthumb1": ["thumb_01_r", "r_thumb1", "righthandthumb1", "cc_base_r_thumb1"],
    "rightthumb2": ["thumb_02_r", "r_thumb2", "righthandthumb2", "cc_base_r_thumb2"],
    "rightthumb3": ["thumb_03_r", "r_thumb3", "righthandthumb3", "cc_base_r_thumb3"],
    "rightindex1": ["index_01_r", "r_index1", "righthandindex1", "cc_base_r_index1"],
    "rightindex2": ["index_02_r", "r_index2", "righthandindex2", "cc_base_r_index2"],
    "rightindex3": ["index_03_r", "r_index3", "righthandindex3", "cc_base_r_index3"],
    "rightmiddle1": ["middle_01_r", "r_mid1", "r_middle1", "righthandmiddle1", "cc_base_r_mid1"],
    "rightmiddle2": ["middle_02_r", "r_mid2", "r_middle2", "righthandmiddle2", "cc_base_r_mid2"],
    "rightmiddle3": ["middle_03_r", "r_mid3", "r_middle3", "righthandmiddle3", "cc_base_r_mid3"],
    "rightring1": ["ring_01_r", "r_ring1", "righthandring1", "cc_base_r_ring1"],
    "rightring2": ["ring_02_r", "r_ring2", "righthandring2", "cc_base_r_ring2"],
    "rightring3": ["ring_03_r", "r_ring3", "righthandring3", "cc_base_r_ring3"],
    "rightpinky1": ["pinky_01_r", "r_pinky1", "little_01_r", "righthandpinky1", "cc_base_r_pinky1"],
    "rightpinky2": ["pinky_02_r", "r_pinky2", "little_02_r", "righthandpinky2", "cc_base_r_pinky2"],
    "rightpinky3": ["pinky_03_r", "r_pinky3", "little_03_r", "righthandpinky3", "cc_base_r_pinky3"],
}


def match_joint(target_name, joint_lookup):
    """Matches target_name against joint_lookup using exact, normalized, or synonym match."""
    if not target_name:
        return None
    if is_helper_bone(target_name):
        return None
    if target_name in joint_lookup:
        return joint_lookup[target_name]

    # Clean namespace / rig prefixes if present
    clean_target = clean_bone_name(target_name)
    if clean_target in joint_lookup:
        return joint_lookup[clean_target]

    norm = normalize_name(target_name)
    if norm in joint_lookup:
        return joint_lookup[norm]

    # Search synonyms
    for canon, aliases in BONE_SYNONYMS.items():
        all_group = [canon] + aliases
        all_group_norm = [normalize_name(a) for a in all_group]
        all_group_clean = [clean_bone_name(a) for a in all_group]
        if norm in all_group_norm or clean_target in all_group_clean or target_name.lower() in all_group:
            for candidate in all_group:
                if candidate in joint_lookup:
                    return joint_lookup[candidate]
                c_clean = clean_bone_name(candidate)
                if c_clean in joint_lookup:
                    return joint_lookup[c_clean]
                c_norm = normalize_name(candidate)
                if c_norm in joint_lookup:
                    return joint_lookup[c_norm]

    # Substring / partial match fallback
    for k, v in joint_lookup.items():
        if norm and len(norm) >= 3 and (norm in k or k in norm):
            return v

    return None


def is_valid_id(x):
    """Safely check if an object, ID, or handle is not None and not null."""
    if x is None:
        return False
    h = getattr(x, "handle", x)
    if hasattr(h, "is_null"):
        try:
            return not h.is_null()
        except Exception:
            pass
    return True


def raw_id(x):
    """Extract raw csc ObjectId/DataId/BehaviourId from wrapped or native object."""
    if x is None:
        return None
    if hasattr(x, "handle"):
        return x.handle
    return x


def apply_pose_to_cascadeur(pose_packet, scene=None):
    """
    Applies incoming Blender pose transforms to Cascadeur active character at target frame.
    Updates both Joint objects and any BoxView rig controllers, ensuring seamless evaluation.
    Supports native Cascadeur execution and simulated mode.
    """
    if not isinstance(pose_packet, dict):
        return False, "Invalid packet format (expected dict)"

class _CascadeurJointState:
    __slots__ = (
        "key",
        "object_id",
        "parent_key",
        "transform_id",
        "model_local_position",
        "model_local_rotation",
        "model_global_transform",
    )

    def __init__(
        self,
        key: str,
        object_id,
        parent_key,
        transform_id,
        model_local_position,
        model_local_rotation,
    ):
        self.key = key
        self.object_id = object_id
        self.parent_key = parent_key
        self.transform_id = transform_id
        self.model_local_position = model_local_position
        self.model_local_rotation = model_local_rotation
        self.model_global_transform = None


def _to_numpy_vec3(value):
    try:
        import numpy as np
        return np.asarray(value, dtype=np.float64)
    except Exception:
        return [float(value[0]), float(value[1]), float(value[2])]


def _make_transform(position, rotation):
    if hasattr(csc.math, "OrthogonalTransform"):
        return csc.math.OrthogonalTransform(
            _to_numpy_vec3(position), rotation.to_quaternion()
        )
    return (position, rotation)


def _build_cascadeur_joint_states(scene):
    """
    Extracts each joint's native bind-pose transforms (model_local_position,
    model_local_rotation) and its parent joint reference from Cascadeur's Node3d graph.
    Matches Cascadeur's official import_bvh_animation implementation.
    """
    states = {}
    try:
        import pycsc
        py_scene = pycsc.wrap(scene)
        node3d_objects = py_scene.get_scene_objects(with_behaviour="Node3d")
        node3d_by_handle = {obj.handle: obj for obj in node3d_objects}

        mv = scene.model_viewer()
        bv = mv.behaviour_viewer()
        joint_ids = bv.get_behaviours("Joint")

        handle_to_name = {}
        for j_id in joint_ids:
            o_id = bv.get_behaviour_owner(j_id)
            if is_valid_id(o_id):
                j_name = mv.get_object_name(o_id) if hasattr(mv, "get_object_name") else mv.object_name(o_id)
                handle_to_name[raw_id(o_id)] = j_name

        for j_id in joint_ids:
            owner_id = bv.get_behaviour_owner(j_id)
            if not is_valid_id(owner_id):
                continue
            r_owner = raw_id(owner_id)
            j_name = handle_to_name.get(r_owner, "")
            node = node3d_by_handle.get(r_owner)
            if node is None:
                continue

            tr_id = bv.get_behaviour_by_name(r_owner, "Transform")
            if not is_valid_id(tr_id):
                continue

            parent_key = None
            basic_id = bv.get_behaviour_by_name(r_owner, "Basic")
            if is_valid_id(basic_id):
                try:
                    p_obj_id = bv.get_behaviour_object(basic_id, "parent")
                    if is_valid_id(p_obj_id):
                        parent_key = handle_to_name.get(raw_id(p_obj_id))
                except Exception:
                    parent_key = None

            if parent_key is None:
                try:
                    parent = node.parent_obj()
                    if parent is not None:
                        parent_key = handle_to_name.get(raw_id(parent.handle))
                except Exception:
                    parent_key = None

            model_lp = _to_numpy_vec3(node.Node3d.model_local_position.get())
            model_lr = node.Node3d.model_local_rotation.get()

            states[j_name] = _CascadeurJointState(
                key=j_name,
                object_id=r_owner,
                parent_key=parent_key,
                transform_id=raw_id(tr_id),
                model_local_position=model_lp,
                model_local_rotation=model_lr,
            )
    except Exception as e:
        print(f"[Live Link] Error building joint states: {e}")
        sys.stdout.flush()
    return states


def _compute_cascadeur_model_globals(states):
    """
    Computes global bind-pose transforms for all joints in states by accumulating
    model_local transforms down the hierarchy.
    """
    def resolve(key: str):
        state = states[key]
        if state.model_global_transform is not None:
            return state.model_global_transform

        local_tf = _make_transform(
            state.model_local_position,
            state.model_local_rotation,
        )

        if state.parent_key is None or state.parent_key not in states:
            state.model_global_transform = local_tf
        else:
            parent_global = resolve(state.parent_key)
            state.model_global_transform = csc.math.combine_transforms(
                parent_global, local_tf
            )

        return state.model_global_transform

    return {key: resolve(key) for key in states.keys()}


_cached_casc_states = None
_cached_casc_globals = None
_cached_casc_bind_mats = None
_cached_casc_bind_inv_quats = None
_last_cached_scene_ref = None


def _ensure_cascadeur_globals(scene):
    """
    Ensures Cascadeur bind pose states and global rotation matrices are precomputed and cached.
    Cached per scene/character and reused across streaming frames for zero per-frame overhead.
    """
    global _cached_casc_states, _cached_casc_globals, _cached_casc_bind_mats, _cached_casc_bind_inv_quats, _last_cached_scene_ref
    scene_ref = id(scene)
    if _cached_casc_globals is None or _cached_casc_bind_inv_quats is None or _last_cached_scene_ref != scene_ref:
        _last_cached_scene_ref = scene_ref
        _cached_casc_states = _build_cascadeur_joint_states(scene)
        _cached_casc_globals = _compute_cascadeur_model_globals(_cached_casc_states)
        _cached_casc_bind_mats = {}
        _cached_casc_bind_inv_quats = {}
        if _cached_casc_globals and HAS_CASCADEUR and csc is not None:
            for j_k, tf_k in _cached_casc_globals.items():
                try:
                    q = extract_quat(tf_k.rotation)
                    # Precompute normalized quaternion inverse: (w, -x, -y, -z)
                    _cached_casc_bind_inv_quats[j_k] = [q[0], -q[1], -q[2], -q[3]]
                    r_k = csc.math.Rotation.from_quaternion(tf_k.rotation)
                    _cached_casc_bind_mats[j_k] = r_k.to_rotation_matrix()
                except Exception:
                    pass
    return _cached_casc_states, _cached_casc_globals, _cached_casc_bind_mats


def apply_pose_to_cascadeur(pose_packet, scene=None):
    """
    Applies an animation pose packet received from Blender to the active Cascadeur character.
    Uses official kinematic retargeting:
      1. Reads native bind pose (model_local_position, model_local_rotation) for joints.
      2. Multiplies Blender 3D delta rotations by Cascadeur's bind orientation:
         M_target_global = R_delta @ M_casc_model_global.
      3. Decomposes M_target_global down Cascadeur's native joint hierarchy:
         M_local = M_parent_global.T @ M_target_global.
         Keeps anatomical model_local_position for child bones (preserving bone length).
      4. Sets values via de.set_data_value and runs scene_updater.run_update(actuals, frame_idx).
    """
    if not pose_packet or not isinstance(pose_packet, dict):
        return False, "Invalid pose packet"

    bones = pose_packet.get("bones", {})
    if not bones:
        return False, "No bones data in pose packet"

    if scene is None:
        if not HAS_CASCADEUR or csc is None:
            return True, f"Simulated pose accepted ({len(bones)} bones)"
        scene = get_active_cascadeur_scene()

    if not scene:
        return False, "No active Cascadeur scene found"

    # 1. Target Frame Resolution
    current_frame = 0
    try:
        if hasattr(scene, "get_current_frame"):
            current_frame = int(scene.get_current_frame())
        elif hasattr(scene, "current_frame"):
            current_frame = int(scene.current_frame())
    except Exception:
        try:
            current_frame = int(scene.current_frame())
        except Exception:
            current_frame = getattr(streamer, "_last_valid_frame", 0)

    pkt_frame = pose_packet.get("frame")
    if pkt_frame is None or pkt_frame == "CURRENT":
        frame_idx = current_frame
    else:
        try:
            frame_idx = int(pkt_frame)
        except Exception:
            frame_idx = current_frame

    if frame_idx < 0:
        return False, 'Cascadeur requer frame inteiro maior ou igual a zero'

    try:
        mv = scene.model_viewer()
        bv = mv.behaviour_viewer()
        joint_ids = bv.get_behaviours("Joint")
        if not joint_ids:
            return False, "No joints found in active scene"

        # Map each joint to any associated BoxView controllers via RigAdditionalInfo
        box_for_joint = {}
        try:
            rig_infos = bv.get_behaviours("RigAdditionalInfo")
            for ri_id in rig_infos:
                try:
                    box_owner = bv.get_behaviour_owner(ri_id)
                    joint_obj = bv.get_behaviour_object(ri_id, "joint")
                    if is_valid_id(joint_obj) and is_valid_id(box_owner):
                        box_for_joint[raw_id(joint_obj)] = raw_id(box_owner)
                except Exception:
                    continue
        except Exception:
            pass

        # Also index existing BoxView controllers for direct name matching
        box_by_name = {}
        try:
            box_ids = bv.get_behaviours("BoxView")
            for b_id in box_ids:
                try:
                    b_owner = bv.get_behaviour_owner(b_id)
                    if not is_valid_id(b_owner):
                        continue
                    b_name = mv.get_object_name(b_owner) if hasattr(mv, "get_object_name") else mv.object_name(b_owner)
                    box_by_name[b_name] = raw_id(b_owner)
                    box_by_name[normalize_name(b_name)] = raw_id(b_owner)
                except Exception:
                    continue
        except Exception:
            pass

        joint_lookup = {}
        for j_id in joint_ids:
            try:
                owner_id = bv.get_behaviour_owner(j_id)
                if not is_valid_id(owner_id):
                    continue
                r_owner = raw_id(owner_id)
                raw_name = mv.get_object_name(owner_id) if hasattr(mv, "get_object_name") else mv.object_name(owner_id)
                tr_id = bv.get_behaviour_by_name(r_owner, "Transform")
                if not is_valid_id(tr_id):
                    continue

                r_tr = raw_id(tr_id)
                lr_id = bv.get_behaviour_data(r_tr, "local_rotation")
                lp_id = bv.get_behaviour_data(r_tr, "local_position")

                # Check if this joint has an associated Box controller
                box_owner = box_for_joint.get(r_owner)
                if not is_valid_id(box_owner):
                    norm_jname = normalize_name(raw_name)
                    box_owner = box_by_name.get(raw_name) or box_by_name.get(norm_jname)

                box_tr_id = None
                box_lr_id = None
                box_lp_id = None
                if is_valid_id(box_owner):
                    r_box = raw_id(box_owner)
                    b_tr = bv.get_behaviour_by_name(r_box, "Transform")
                    if is_valid_id(b_tr):
                        box_tr_id = raw_id(b_tr)
                        b_lr = bv.get_behaviour_data(box_tr_id, "local_rotation")
                        b_lp = bv.get_behaviour_data(box_tr_id, "local_position")
                        box_lr_id = raw_id(b_lr) if is_valid_id(b_lr) else None
                        box_lp_id = raw_id(b_lp) if is_valid_id(b_lp) else None

                entry = {
                    "owner_id": r_owner,
                    "name": raw_name,
                    "tr_id": r_tr,
                    "lr_id": raw_id(lr_id) if is_valid_id(lr_id) else None,
                    "lp_id": raw_id(lp_id) if is_valid_id(lp_id) else None,
                    "box_owner_id": raw_id(box_owner) if is_valid_id(box_owner) else None,
                    "box_tr_id": box_tr_id,
                    "box_lr_id": box_lr_id,
                    "box_lp_id": box_lp_id,
                }
                joint_lookup[raw_name] = entry
                joint_lookup[normalize_name(raw_name)] = entry
            except Exception:
                continue

        # Kinematic retargeting solver preparation:
        has_delta_rot = any("delta_rot" in bd for bd in bones.values())
        final_local_rotations = {}
        final_local_positions = {}
        states = {}

        if has_delta_rot and HAS_CASCADEUR and csc is not None:
            states, model_globals, _ = _ensure_cascadeur_globals(scene)

            if states and model_globals:
                ordered_keys = []
                visited = set()

                def _visit(k):
                    if k in visited or k not in states:
                        return
                    p = states[k].parent_key
                    if p and p in states and p not in visited:
                        _visit(p)
                    visited.add(k)
                    ordered_keys.append(k)

                for k in states:
                    _visit(k)

                # Phase 3: Target global orientations
                target_global_rotations = {}
                target_global_positions = {}

                for j_name in ordered_keys:
                    state = states[j_name]
                    j_info = match_joint(j_name, joint_lookup)
                    bdata = None
                    if j_info:
                        # For root joint, prioritize the incoming bone explicitly marked as is_root
                        if state.parent_key is None:
                            for bname, bd in bones.items():
                                if bd.get("is_root") and match_joint(bname, joint_lookup) == j_info:
                                    bdata = bd
                                    break
                        if bdata is None:
                            for bname, bd in bones.items():
                                if not is_helper_bone(bname) and match_joint(bname, joint_lookup) == j_info:
                                    bdata = bd
                                    break
                    if bdata is None:
                        bdata = bones.get(j_name) or bones.get(normalize_name(j_name))

                    if not bdata:
                        continue

                    dq = bdata.get("delta_rot")
                    if dq and len(dq) == 4:
                        try:
                            q_delta = csc.math.Quaternion(float(dq[0]), float(dq[1]), float(dq[2]), float(dq[3]))
                            r_delta = csc.math.Rotation.from_quaternion(q_delta)
                            delta_mat = r_delta.to_rotation_matrix()

                            casc_model_global_rot = csc.math.Rotation.from_quaternion(model_globals[j_name].rotation)
                            casc_model_global_mat = casc_model_global_rot.to_rotation_matrix()

                            target_mat = delta_mat @ casc_model_global_mat
                            target_global_rotations[j_name] = csc.math.Rotation.from_rotation_matrix(target_mat)
                        except Exception:
                            pass

                    g_pos = bdata.get("g_pos") or bdata.get("pos")
                    if g_pos and len(g_pos) >= 3:
                        target_global_positions[j_name] = _to_numpy_vec3(g_pos)

                # Phase 4: Decompose to local transforms down hierarchy
                casc_anim_globals = {}

                # Seed casc_anim_globals with model_globals for unposed parent joints (such as CC_Base_BoneRoot)
                for k in ordered_keys:
                    if k not in target_global_rotations and k in model_globals:
                        casc_anim_globals[k] = model_globals[k]

                for j_name in ordered_keys:
                    state = states[j_name]
                    target_rot = target_global_rotations.get(j_name)
                    if target_rot is None:
                        continue

                    target_pos = target_global_positions.get(j_name)

                    # Identify if this joint is marked as the root bone
                    bdata = None
                    j_info = match_joint(j_name, joint_lookup)
                    if j_info:
                        for bname, bd in bones.items():
                            if match_joint(bname, joint_lookup) == j_info:
                                bdata = bd
                                break
                    if bdata is None:
                        bdata = bones.get(j_name) or bones.get(normalize_name(j_name))

                    is_root_bone = (state.parent_key is None) or (bdata and bdata.get("is_root", False))
                    is_unposed_bone = bool(bdata and bdata.get("is_unposed", False))

                    if is_unposed_bone and not is_root_bone:
                        # Unposed joint in Blender: preserve exact anatomical model_local_rotation without numerical drift
                        local_rot = state.model_local_rotation
                        local_pos = state.model_local_position
                    elif state.parent_key is None or state.parent_key not in casc_anim_globals:
                        # Top-level root joint without parent: target global rotation and target global position
                        local_rot = target_rot
                        local_pos = target_pos if target_pos is not None else state.model_local_position
                    else:
                        # Child joint: decompose relative to parent's global orientation
                        parent_global = casc_anim_globals[state.parent_key]
                        parent_global_rot = csc.math.Rotation.from_quaternion(
                            parent_global.rotation
                        )
                        local_rot_mat = (
                            parent_global_rot.to_rotation_matrix().T
                            @ target_rot.to_rotation_matrix()
                        )
                        local_rot = csc.math.Rotation.from_rotation_matrix(local_rot_mat)

                        if is_root_bone and target_pos is not None:
                            # Designated root bone under an unposed helper joint (e.g. CC_Base_Hip under CC_Base_BoneRoot)
                            parent_pos = getattr(parent_global, "position", [0.0, 0.0, 0.0])
                            pos_diff = target_pos - _to_numpy_vec3(parent_pos)
                            local_pos = parent_global_rot.to_rotation_matrix().T @ pos_diff
                        else:
                            # Fixed FK bone offset preserves bone length
                            local_pos = state.model_local_position

                    local_tf = _make_transform(local_pos, local_rot)
                    if state.parent_key is None or state.parent_key not in casc_anim_globals:
                        global_tf = local_tf
                    else:
                        global_tf = csc.math.combine_transforms(
                            casc_anim_globals[state.parent_key],
                            local_tf,
                        )
                    casc_anim_globals[j_name] = global_tf
                    final_local_rotations[j_name] = local_rot
                    final_local_positions[j_name] = local_pos

        matched_count = 0
        callback_error = None

        def mod(*args, **kwargs):
            nonlocal matched_count, callback_error
            try:
                model = args[0] if len(args) > 0 else kwargs.get("model")
                update = args[1] if len(args) > 1 else kwargs.get("update")
                scene_updater = args[2] if len(args) > 2 else kwargs.get("scene_updater")

                lv = scene.layers_viewer() if hasattr(scene, "layers_viewer") else None
                le = model.layers_editor() if hasattr(model, "layers_editor") else None
                de = model.data_editor() if hasattr(model, "data_editor") else None
                actuals = set()
                mapped_owners = []

                # Extend animation storage BEFORE DataEditor writes. Deleting the
                # final keys can collapse it to frame 0 even if Blender still has
                # keys at later frames. A trailing empty section is not a key.
                owners = [raw_id(info['owner_id']) for info in joint_lookup.values()]
                owners += [raw_id(info['box_owner_id']) for info in joint_lookup.values()
                           if is_valid_id(info.get('box_owner_id'))]
                target_layers = list(lv.layer_ids_by_obj_ids(owners))
                extended = False
                for layer_id in target_layers:
                    if frame_idx >= lv.frames_count([layer_id]):
                        le.set_section(csc.layers.layer.Section(), frame_idx + 1, raw_id(layer_id))
                        extended = True
                if extended:
                    model.fit_animation_size_by_layers()
                    scene_updater.generate_update()
                for layer_id in target_layers:
                    le.set_fixed_interpolation_or_key_if_need(raw_id(layer_id), frame_idx, True)

                if final_local_rotations and de:
                    # Method 1: Official kinematic retargeting path (calibration-free)
                    for j_name, local_rot in final_local_rotations.items():
                        state = states[j_name]
                        mapped_owners.append(state.object_id)
                        matched_count += 1

                        lr_id = bv.get_behaviour_data(state.transform_id, "local_rotation")
                        if is_valid_id(lr_id):
                            de.set_data_value(raw_id(lr_id), frame_idx, local_rot)
                            actuals.add(raw_id(lr_id))

                        # Synchronize associated BoxView rig controller if present for ANY joint
                        box_owner = box_for_joint.get(raw_id(state.object_id))
                        if is_valid_id(box_owner):
                            r_box = raw_id(box_owner)
                            mapped_owners.append(r_box)
                            b_tr = bv.get_behaviour_by_name(r_box, "Transform")
                            if is_valid_id(b_tr):
                                b_lr = bv.get_behaviour_data(raw_id(b_tr), "local_rotation")
                                if is_valid_id(b_lr):
                                    de.set_data_value(raw_id(b_lr), frame_idx, local_rot)
                                    actuals.add(raw_id(b_lr))

                        bdata = None
                        j_info = match_joint(j_name, joint_lookup)
                        if j_info:
                            for bname, bd in bones.items():
                                if match_joint(bname, joint_lookup) == j_info:
                                    bdata = bd
                                    break
                        if bdata is None:
                            bdata = bones.get(j_name) or bones.get(normalize_name(j_name))

                        is_root = (state.parent_key is None) or (bdata and bdata.get("is_root", False))
                        if is_root:
                            lp_id = bv.get_behaviour_data(state.transform_id, "local_position")
                            local_pos = final_local_positions.get(j_name)
                            if is_valid_id(lp_id) and local_pos is not None:
                                de.set_data_value(raw_id(lp_id), frame_idx, _to_numpy_vec3(local_pos))
                                actuals.add(raw_id(lp_id))

                            if is_valid_id(box_owner):
                                r_box = raw_id(box_owner)
                                b_tr = bv.get_behaviour_by_name(r_box, "Transform")
                                if is_valid_id(b_tr):
                                    b_lp = bv.get_behaviour_data(raw_id(b_tr), "local_position")
                                    if is_valid_id(b_lp) and local_pos is not None:
                                        de.set_data_value(raw_id(b_lp), frame_idx, _to_numpy_vec3(local_pos))
                                        actuals.add(raw_id(b_lp))

                else:
                    # Method 2: Fallback direct mapping path
                    for bname, bdata in bones.items():
                        j_info = match_joint(bname, joint_lookup)
                        if not j_info:
                            continue

                        owner_id = j_info["owner_id"]
                        box_owner_id = j_info.get("box_owner_id")
                        mapped_owners.append(owner_id)
                        if is_valid_id(box_owner_id):
                            mapped_owners.append(box_owner_id)
                        matched_count += 1

                        rot = bdata.get("rot")
                        rot_obj = None
                        if rot and len(rot) == 4:
                            try:
                                if hasattr(csc.math.Rotation, "from_quaternion"):
                                    try:
                                        rot_obj = csc.math.Rotation.from_quaternion(
                                            float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3])
                                        )
                                    except TypeError:
                                        q = csc.math.Quaternion(float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3]))
                                        rot_obj = csc.math.Rotation.from_quaternion(q)
                                else:
                                    rot_obj = csc.math.Quaternion(float(rot[0]), float(rot[1]), float(rot[2]), float(rot[3]))
                            except Exception:
                                pass

                        pos = bdata.get("pos")
                        is_root = any(k in bname.lower() for k in ("hip", "pelvis", "root"))
                        if is_root and (pos is None or all(abs(v) < 1e-4 for v in pos)):
                            g_pos_val = bdata.get("g_pos")
                            if g_pos_val and any(abs(v) > 1e-4 for v in g_pos_val):
                                pos = g_pos_val

                        pos_arr = None
                        if pos and len(pos) >= 3:
                            if is_root or any(abs(v) > 1e-4 for v in pos):
                                pos_arr = _to_numpy_vec3(pos)

                        update_items = [
                            (owner_id, j_info["lr_id"], j_info["lp_id"]),
                        ]
                        if is_root and is_valid_id(box_owner_id):
                            update_items.append((box_owner_id, j_info.get("box_lr_id"), j_info.get("box_lp_id")))

                        for target_obj_id, target_lr_id, target_lp_id in update_items:
                            if not is_valid_id(target_obj_id):
                                continue

                            if de:
                                if is_valid_id(target_lr_id) and rot_obj is not None:
                                    try:
                                        de.set_data_value(raw_id(target_lr_id), frame_idx, rot_obj)
                                        actuals.add(raw_id(target_lr_id))
                                    except Exception:
                                        pass
                                if is_valid_id(target_lp_id) and pos_arr is not None:
                                    try:
                                        de.set_data_value(raw_id(target_lp_id), frame_idx, pos_arr)
                                        actuals.add(raw_id(target_lp_id))
                                    except Exception:
                                        pass

                if actuals:
                    try:
                        if lv and le:
                            key_layer_ids = list(lv.layer_ids_by_obj_ids(mapped_owners))
                            if not key_layer_ids:
                                key_layer_ids = [lid for lid in lv.all_layer_ids() if is_valid_id(lid)]
                            for layer_id in key_layer_ids:
                                try:
                                    le.set_fixed_interpolation_or_key_if_need(raw_id(layer_id), frame_idx, True)
                                except Exception:
                                    pass
                        if hasattr(model, "set_fixed_interpolation_if_need"):
                            try:
                                model.set_fixed_interpolation_if_need(actuals, frame_idx)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    if scene_updater:
                        try:
                            scene_updater.run_update(actuals, int(frame_idx))
                        except Exception as e:
                            print(f"[Live Link] run_update error: {e}")
                            sys.stdout.flush()

            except Exception as e:
                callback_error = str(e)
                import traceback
                print(f"[Live Link Error inside mod]: {e}\n{traceback.format_exc()}")
                sys.stdout.flush()
                raise

        ok = scene.modify_update("Apply Pose from Blender", mod)
        if ok:
            # Force playhead to target frame so viewport immediately shows new pose
            try:
                if hasattr(scene, "set_current_frame"):
                    scene.set_current_frame(frame_idx)
                elif hasattr(scene, "current_frame"):
                    scene.current_frame(frame_idx)
            except Exception:
                pass

            msg = f"Successfully applied pose ({matched_count} bones) at frame {frame_idx}"
            try:
                if hasattr(scene, "success"):
                    scene.success(f"[Live Link] {msg}")
            except Exception:
                pass
            return True, msg
        else:
            err_msg = f'Frame {frame_idx}: ' + (callback_error or 'Cascadeur rejeitou a alteracao; consulte o log')
            try:
                if hasattr(scene, "error"):
                    scene.error(f"[Live Link Error] {err_msg}")
            except Exception:
                pass
            return False, err_msg

    except Exception as e:
        err_str = f"Error applying pose to scene: {e}"
        try:
            if hasattr(scene, "error"):
                scene.error(f"[Live Link Error] {err_str}")
        except Exception:
            pass
        return False, err_str


def call_cascadeur_action(action_name):
    """Call a Cascadeur UI action safely via ActionManager."""
    try:
        if not HAS_CASCADEUR or csc is None:
            return False
        app = csc.app.get_application()
        if app and hasattr(app, "get_action_manager"):
            am = app.get_action_manager()
            if am and hasattr(am, "call_action"):
                try:
                    am.call_action(action_name)
                    return True
                except Exception:
                    try:
                        am.call_action(f"Actions.{action_name}")
                        return True
                    except Exception:
                        pass
    except Exception as e:
        print(f"[Live Link] Action error for '{action_name}': {e}")
    return False


class CascadeurPoseReceiver:
    """
    Receives poses and timeline commands sent from Blender via UDP (default port 9998).
    Runs on a lightweight background thread and dispatches execution to Cascadeur's
    Qt event loop only when packets arrive, ensuring ZERO CPU overhead when idle.
    """
    def __init__(self, host="127.0.0.1", port=9998):
        self.host = host
        self.port = port
        self.sock = None
        self.is_running = False
        self.poses_applied = 0
        self.commands_executed = 0
        self.last_pose_time = 0.0
        self.last_received_packet = None
        self.last_error = ""
        self._qtimer = None
        self._thread = None
        self._stop_event = threading.Event()

    def start(self):
        if self.is_running:
            return f"Pose Receiver is already running on {self.host}:{self.port}"

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            bind_host = self.host if self.host else "127.0.0.1"
            self.sock.bind((bind_host, self.port))
            self.sock.setblocking(False)
        except Exception as e:
            self.last_error = f"Socket bind error: {e}"
            msg = f"Failed to bind UDP port {self.port}: {e}"
            print(msg)
            return msg

        self.is_running = True
        self._stop_event.clear()

        # Mode A: Native Qt QTimer on Main GUI Thread (runs inside Cascadeur)
        if HAS_QT and HAS_CASCADEUR:
            self._qtimer = QtCore.QTimer()
            self._qtimer.setInterval(20)  # 50 Hz polling (takes < 0.002ms when idle)
            self._qtimer.timeout.connect(self._poll_incoming)
            self._qtimer.start()

            import builtins
            builtins._cascadeur_pose_receiver_qtimer = self._qtimer

            msg = f"✓ Cascadeur Pose & Command Receiver STARTED (Qt Main-Thread)! Listening on UDP {self.host}:{self.port}."
            print(msg)
            sys.stdout.flush()
            return msg

        # Mode B: Background Thread (for headless/standalone testing outside Cascadeur)
        self.sock.setblocking(True)
        self.sock.settimeout(0.3)
        self._thread = threading.Thread(target=self._thread_loop, daemon=True)
        self._thread.start()
        msg = f"✓ Cascadeur Pose & Command Receiver STARTED (Thread mode)! Listening on UDP {self.host}:{self.port}."
        print(msg)
        sys.stdout.flush()
        return msg

    def _poll_incoming(self):
        """Poll non-blocking UDP socket directly on Cascadeur's Qt main GUI thread."""
        if not self.is_running or not self.sock:
            return

        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
                if not data:
                    break
                packet = json.loads(data.decode("utf-8"))
                self._handle_packet(packet)
            except (BlockingIOError, socket.error):
                break
            except Exception as e:
                self.last_error = f"Packet decode error: {e}"
                break

    def _thread_loop(self):
        """Worker loop for standalone test mode outside Cascadeur."""
        while self.is_running and not self._stop_event.is_set():
            try:
                data, addr = self.sock.recvfrom(65535)
                if not data:
                    continue
                packet = json.loads(data.decode("utf-8"))
                self._handle_packet(packet)
            except socket.timeout:
                continue
            except Exception as e:
                self.last_error = f"Receive error: {e}"

    def _handle_packet(self, packet):
        """Execute command or apply pose directly on Cascadeur's main thread."""
        try:
            cmd = packet.get("cmd")

            if cmd == 'live_start':
                streamer.port = int(packet.get('stream_port', 9999))
                streamer._joint_map = []
                streamer.start()
                return

            if cmd == 'keyframe_mode':
                streamer.stop()
                pose_receiver.start()
                return

            if cmd == 'keyframe_apply_pose':
                ok, message = apply_pose_to_cascadeur(packet)
                key_layers = []
                if ok:
                    active = get_active_cascadeur_scene()
                    lv = active.layers_viewer()
                    key_layers = [str(lid) for lid in lv.all_layer_ids()
                                  if int(packet['frame']) in list(lv.layer(lid).key_frame_indices())]
                    if not key_layers:
                        ok, message = False, 'A pose foi aplicada, mas nenhuma chave foi confirmada no frame solicitado'
                reply = {'request_id': packet.get('request_id'), 'frame': packet.get('frame'),
                         'ok': ok, 'message': message, 'key_layers':len(key_layers), 'error': '' if ok else message}
                self.sock.sendto(json.dumps(reply).encode(), ('127.0.0.1', int(packet['reply_port'])))
                return

            if cmd == "keyframe_snapshot":
                reply = {"request_id": packet.get("request_id"), "frame": packet.get("frame")}
                reply_port = int(packet["reply_port"])
                try:
                    scene = get_active_cascadeur_scene()
                    if scene is None:
                        raise ValueError("Nenhuma cena ativa no Cascadeur")
                    frame = int(packet["frame"])
                    scene.set_current_frame(frame)
                    if int(scene.get_current_frame()) != frame:
                        raise ValueError("Frame fora do intervalo da cena Cascadeur")
                    snapshot = streamer.get_cascadeur_joint_data(scene, frame)
                    if not snapshot.get("bones"):
                        raise ValueError("Nenhum joint encontrado na cena")
                    bones = list(snapshot["bones"].items())
                    parts = [dict(bones[i:i+20]) for i in range(0, len(bones), 20)]
                    for index, part in enumerate(parts):
                        outgoing = dict(reply, bones=part, part=index, parts=len(parts))
                        self.sock.sendto(json.dumps(outgoing, cls=SafeJSONEncoder).encode(), ("127.0.0.1", reply_port))
                except Exception as error:
                    self.sock.sendto(json.dumps(dict(reply, error=str(error))).encode(), ("127.0.0.1", reply_port))
                return

            # 1. Reload command
            if cmd == "reload":
                import importlib
                import cascadeur_streamer
                cascadeur_streamer.stop_all()
                importlib.reload(cascadeur_streamer)
                cascadeur_streamer.start()
                print("[Cascadeur Live Link] ✓ Successfully reloaded cascadeur_streamer!")
                sys.stdout.flush()
                return

            # 2. Timeline navigation and playback commands
            if cmd and cmd.startswith("timeline_"):
                self._handle_timeline_command(cmd, packet)
                return

            # 3. Pose application
            if "bones" in packet:
                self.last_received_packet = packet
                self.last_pose_time = time.time()
                ok, msg = apply_pose_to_cascadeur(packet)
                if ok:
                    self.poses_applied += 1
                    print(f"[Cascadeur Live Link] {msg}")
                else:
                    self.last_error = msg
                    print(f"[Cascadeur Live Link] Failed to apply pose: {msg}")
                sys.stdout.flush()

        except Exception as e:
            self.last_error = f"Packet handling error: {e}"
            print(f"[Cascadeur Live Link] Handler error: {e}")
            sys.stdout.flush()

    def _handle_timeline_command(self, cmd, packet):
        """Execute timeline actions directly in Cascadeur on the GUI thread."""
        scene = get_active_cascadeur_scene()
        self.commands_executed += 1

        if cmd == "timeline_play_pause":
            ok = call_cascadeur_action("Timeline.Play")
            if not ok:
                call_cascadeur_action("Timeline.Keyframe play")
            print("[Cascadeur Live Link] Timeline: Play/Pause toggled")

        elif cmd == "timeline_to_first":
            ok = call_cascadeur_action("Timeline.To first frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To begin")
            if not ok and scene and hasattr(scene, "set_current_frame"):
                scene.set_current_frame(0)
            print("[Cascadeur Live Link] Timeline: Jumped to first frame")

        elif cmd == "timeline_to_last":
            ok = call_cascadeur_action("Timeline.To last frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To end")
            print("[Cascadeur Live Link] Timeline: Jumped to last frame")

        elif cmd == "timeline_next_frame":
            ok = call_cascadeur_action("Timeline.Next frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To next frame")
            if not ok and scene and hasattr(scene, "get_current_frame") and hasattr(scene, "set_current_frame"):
                scene.set_current_frame(int(scene.get_current_frame()) + 1)
            print("[Cascadeur Live Link] Timeline: Next frame")

        elif cmd == "timeline_prev_frame":
            ok = call_cascadeur_action("Timeline.Previous frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To previous frame")
            if not ok and scene and hasattr(scene, "get_current_frame") and hasattr(scene, "set_current_frame"):
                scene.set_current_frame(max(0, int(scene.get_current_frame()) - 1))
            print("[Cascadeur Live Link] Timeline: Previous frame")

        elif cmd == "timeline_next_key":
            ok = call_cascadeur_action("Timeline.Next key frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To next key")
            print("[Cascadeur Live Link] Timeline: Next keyframe")

        elif cmd == "timeline_prev_key":
            ok = call_cascadeur_action("Timeline.Previous key frame")
            if not ok:
                ok = call_cascadeur_action("Timeline.To previous key")
            print("[Cascadeur Live Link] Timeline: Previous keyframe")

        elif cmd == "timeline_set_frame":
            target_f = int(packet.get("frame", 0))
            if scene and hasattr(scene, "set_current_frame"):
                scene.set_current_frame(target_f)
            else:
                try:
                    import pycsc
                    pycsc.get_current_scene().set_current_frame(target_f)
                except Exception:
                    pass
            print(f"[Cascadeur Live Link] Timeline: Jumped to frame {target_f}")

        sys.stdout.flush()

    def stop(self):
        if not self.is_running:
            return "Cascadeur Pose Receiver is not running."

        self.is_running = False
        self._stop_event.set()

        if self._qtimer is not None:
            try:
                self._qtimer.stop()
                self._qtimer.timeout.disconnect(self._poll_incoming)
            except Exception:
                pass
            self._qtimer = None
            import builtins
            builtins._cascadeur_pose_receiver_qtimer = None

        if self._thread is not None:
            self._thread.join(timeout=0.6)
            self._thread = None

        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        msg = f"✓ Cascadeur Pose & Command Receiver STOPPED. (Poses: {self.poses_applied}, Commands: {self.commands_executed})"
        print(msg)
        sys.stdout.flush()
        return msg


# Global streamer and receiver instances
streamer = CascadeurStreamer()
pose_receiver = CascadeurPoseReceiver()


def start_pose_receiver(port=9998):
    """Start listening for incoming poses from Blender on port 9998."""
    global pose_receiver
    pose_receiver.port = port
    return pose_receiver.start()


def stop_pose_receiver():
    """Stop listening for incoming poses from Blender."""
    global pose_receiver
    return pose_receiver.stop()


def status():
    """Check current streamer and receiver status."""
    recv_str = f"Listening on port {pose_receiver.port} (Poses: {pose_receiver.poses_applied})" if pose_receiver.is_running else "Stopped"
    return (
        f"\n{'=' * 40}\n"
        f"CASCADEUR LIVE LINK STATUS:\n"
        f"  Streamer:       {'Running' if streamer.is_running else 'Stopped'} ({streamer.host}:{streamer.port}, Packets: {streamer.packets_sent})\n"
        f"  Pose Receiver:  {recv_str}\n"
        f"  Engine Mode:    {'PySide6 Qt Main-Thread Timer' if (HAS_QT and HAS_CASCADEUR) else 'Background Thread'}\n"
        f"  Cascadeur API:  {'Detected (csc)' if HAS_CASCADEUR else 'Simulated Mode'}\n"
        f"  Active Bones:   {len(streamer._joint_map)}\n"
        f"  Last Frame:     {streamer._last_valid_frame}\n"
        f"{'=' * 40}\n"
    )


def test():
    """Sample a single frame and display stats."""
    data = streamer.get_cascadeur_joint_data()
    bones = data.get("bones", {})
    sample_names = list(bones.keys())[:8]
    error_note = f"\n  (Internal error: {streamer.last_error})" if streamer.last_error else ""
    return (
        f"✓ Frame: {data.get('frame')} | Total Bones: {len(bones)}\n"
        f"  Sample Bones: {sample_names}{error_note}"
    )


def ping():
    """Send a single frame packet right now and report results."""
    data = streamer.get_cascadeur_joint_data()
    try:
        payload = json.dumps(data, cls=SafeJSONEncoder).encode("utf-8")
        bytes_sent = streamer.sock.sendto(payload, (streamer.host, streamer.port))
        bones = data.get("bones", {})
        sample_bone = next(iter(bones.items())) if bones else None
        return (
            f"✓ Ping sent {bytes_sent} bytes to {streamer.host}:{streamer.port}!\n"
            f"  Frame: {data.get('frame')}\n"
            f"  Bones Extracted: {len(bones)}\n"
            f"  Sample Bone: {sample_bone}"
        )
    except Exception as e:
        return f"✗ Socket send error: {e}"


def stop_all():
    """Stop all running CascadeurStreamer and CascadeurPoseReceiver instances across the session."""
    count = 0
    for obj in gc.get_objects():
        if type(obj).__name__ in ("CascadeurStreamer", "CascadeurPoseReceiver"):
            try:
                obj.stop()
                count += 1
            except Exception:
                obj.is_running = False

    global pose_receiver, streamer
    if pose_receiver is not None and pose_receiver.is_running:
        try:
            pose_receiver.stop()
        except Exception:
            pose_receiver.is_running = False

    if streamer is not None and streamer.is_running:
        try:
            streamer.stop()
        except Exception:
            streamer.is_running = False

    try:
        import builtins
        if hasattr(builtins, "_cascadeur_streamer_qtimer") and builtins._cascadeur_streamer_qtimer:
            builtins._cascadeur_streamer_qtimer.stop()
            builtins._cascadeur_streamer_qtimer = None
        if hasattr(builtins, "_cascadeur_pose_receiver_qtimer") and builtins._cascadeur_pose_receiver_qtimer:
            builtins._cascadeur_pose_receiver_qtimer.stop()
            builtins._cascadeur_pose_receiver_qtimer = None
    except Exception:
        pass

    global _cached_casc_states, _cached_casc_globals, _cached_casc_bind_mats, _cached_casc_bind_inv_quats
    _cached_casc_states = None
    _cached_casc_globals = None
    _cached_casc_bind_mats = None
    _cached_casc_bind_inv_quats = None

    try:
        import events
        events.scene_idle_manager.unsubscribe_all()
    except Exception:
        pass

    msg = f"✓ Stopped {count} active streamer/receiver instance(s) and cleared timers."
    print(msg)
    return msg


def start(host="127.0.0.1", streamer_port=9999, receiver_port=9998, fps=60):
    """Start both streamer (Cascadeur -> Blender) and pose/command receiver (Blender -> Cascadeur)."""
    global streamer, pose_receiver
    stop_all()
    streamer.host = host
    streamer.port = streamer_port
    streamer.fps = fps
    pose_receiver.host = host
    pose_receiver.port = receiver_port

    s_msg = streamer.start()
    r_msg = pose_receiver.start()
    msg = f"{s_msg}\n{r_msg}"
    return msg


def stop():
    """Stop both streamer and receiver."""
    return stop_all()


def send_rest_pose(frame_num=0):
    """
    Extracts transforms from Cascadeur at frame_num (default 0) and broadcasts a dedicated
    'rest_pose' packet to Blender for T-pose / Rest Pose calibration.
    """
    global streamer
    data = streamer.get_cascadeur_joint_data(scene=None, frame_num=frame_num)
    data["type"] = "rest_pose"
    payload = json.dumps(data).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (streamer.host, streamer.port))
        msg = f"✓ Sent Rest Pose packet ({len(data.get('bones', {}))} bones) at frame {frame_num} to {streamer.host}:{streamer.port}"
        print(msg)
        return msg
    except Exception as e:
        err = f"Failed to send rest pose: {e}"
        print(err)
        return err
    finally:
        sock.close()


def diagnose_rotations():
    """Print rotation values for sample bones to help verify coordinate orientation."""
    data = streamer.get_cascadeur_joint_data()
    bones = data.get("bones", {})
    lines = ["\n=== BONE ROTATION VALUES (Cascadeur) ==="]
    lines.append(f"Current Frame: {data.get('frame')}")
    lines.append(f"Total Bones: {len(bones)}")
    sample_keys = [k for k in bones.keys() if any(sub in k.lower() for sub in ("hip", "pelvis", "spine", "arm", "leg", "head", "root"))][:10]
    if not sample_keys:
        sample_keys = list(bones.keys())[:10]
    for k in sample_keys:
        b = bones[k]
        rot = [round(v, 4) for v in b.get("rot", [])]
        pos = [round(v, 4) for v in b.get("pos", [])]
        lines.append(f"  {k:20s} rot(w,x,y,z): {rot} | pos: {pos}")
    lines.append("=========================================\n")
    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    print("Starting test stream. Press Ctrl+C to stop...")
    streamer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        streamer.stop()
