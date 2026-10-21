import collections
import json
import math
import queue
import socket
import threading
import time
import base64
import zlib
import bpy
from mathutils import Matrix, Quaternion, Vector


# Global thread and queue references
_receiver_thread = None
_stop_event = threading.Event()
_packet_queue = queue.Queue(maxsize=256)
_packet_arrival_times = collections.deque(maxlen=120)
_active_socket = None
_timer_registered = False
_last_frame_applied = -1
_last_packet_received = None
_last_preview = None
_initial_root_pos = None
_scale_auto_detected = False
_send_socket = None


def get_send_socket():
    """Retrieve or create persistent UDP sender socket."""
    global _send_socket
    if _send_socket is None:
        _send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return _send_socket


def udp_listener_worker(host, port, packet_q, stop_evt):
    """Background worker thread to receive UDP datagrams without blocking Blender."""
    assemblies = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)

    # In Windows, binding to 0.0.0.0 ensures reception across localhost and all adapters
    bind_host = "0.0.0.0" if host in ("127.0.0.1", "localhost") else host
    try:
        sock.bind((bind_host, port))
    except Exception:
        try:
            sock.bind((host, port))
        except Exception as e:
            print(f"[Cascadeur Live Link] Failed to bind {host}:{port}: {e}")
            return

    while not stop_evt.is_set():
        try:
            data, addr = sock.recvfrom(65535)
            if not data:
                continue

            try:
                parsed = json.loads(data.decode("utf-8"))
                if parsed.get('type') == 'bridge_chunk':
                    now = time.monotonic()
                    assemblies = {k:v for k,v in assemblies.items() if now-v[0]<5}
                    count, index = parsed['parts'], parsed['part']
                    if not 1 <= count <= 1024 or not 0 <= index < count:
                        continue
                    item = assemblies.setdefault(parsed['id'], (now, {}))
                    item[1][index] = parsed['data']
                    if len(item[1]) != count:
                        continue
                    packed = ''.join(item[1][i] for i in range(count))
                    parsed = json.loads(zlib.decompress(base64.b64decode(packed)))
                    assemblies.pop(parsed.get('revision'), None)
                # If queue is full, drop oldest packet to keep low latency
                if packet_q.full():
                    try:
                        packet_q.get_nowait()
                    except queue.Empty:
                        pass
                packet_q.put_nowait(parsed)
                _packet_arrival_times.append(time.time())
            except Exception as json_err:
                print(f"[Cascadeur Live Link] Packet decode error: {json_err}")
        except socket.timeout:
            continue
        except Exception:
            break

    try:
        sock.close()
    except Exception:
        pass


def is_spine_or_head_bone(name):
    """Detects spine, torso, neck, and head bones that point UP in Blender."""
    if not name:
        return False
    lower = name.lower()
    return any(k in lower for k in ("spine", "stomach", "chest", "neck", "head", "torso", "pelvis", "hips"))


def is_leg_bone(name):
    """Detects leg, thigh, calf, shin, and foot bones that point DOWN in Blender."""
    if not name:
        return False
    lower = name.lower()
    return any(k in lower for k in ("thigh", "calf", "leg", "shin", "foot", "toe", "upleg", "knee"))


def convert_transforms(pos_list, rot_list, mode="Y_UP_TO_Z_UP", is_root=False, settings=None, is_right=False, bone_name=""):
    """
    Convert position and quaternion rotation according to coordinate mapping settings.
    pos_list: [x, y, z]
    rot_list: [w, x, y, z] or [x, y, z, w]
    mode: 'DIRECT', 'Y_UP_TO_Z_UP', 'Z_UP'
    is_root: True if this bone is the root/hips bone, False for child bones
    is_right: True if this bone is on the right side of the body (for symmetry correction)
    bone_name: Optional bone identifier for anatomical branch-specific corrections
    """
    # Normalize quaternion format to (w, x, y, z)
    if len(rot_list) == 4:
        # Standard: [w, x, y, z]
        w, x, y, z = rot_list[0], rot_list[1], rot_list[2], rot_list[3]
    else:
        w, x, y, z = 1.0, 0.0, 0.0, 0.0

    quat = Quaternion((w, x, y, z)).normalized()
    pos = Vector(pos_list) if len(pos_list) == 3 else Vector((0.0, 0.0, 0.0))

    if mode == "Y_UP_TO_Z_UP":
        if is_root:
            # Root bone represents global placement:
            # Convert position from Cascadeur Y-Up (X-right, Y-up, Z-fwd) to Blender Z-Up (X-right, Y-fwd, Z-up)
            pos = Vector((pos.x, -pos.z, pos.y))
            # Rotate root 90 degrees around X-axis onto Blender's ground plane
            conv_rot = Quaternion((math.sqrt(0.5), math.sqrt(0.5), 0.0, 0.0))
            quat = (conv_rot @ quat).normalized()
        else:
            # Child bones are in Parent Space!
            pass

    return pos, quat


def is_bone_right_side(name):
    """
    Detects if a bone belongs to the right side of the body across naming conventions:
    - Suffixes: _r, .r, -r, _R, .R, -R
    - Prefixes: r_, r., r-, R_, R., R-
    - Words/tokens: right, Right, RIGHT, _r_, .r.
    - CC5 tokens: CC_Base_R_...
    """
    if not name:
        return False
    lower = name.lower()
    for suffix in ("_r", ".r", "-r"):
        if lower.endswith(suffix):
            return True
    for prefix in ("r_", "r.", "r-"):
        if lower.startswith(prefix) or f":{prefix}" in lower:
            return True
    if "_r_" in lower or ".r." in lower or "-r-" in lower or "right" in lower:
        return True
    tokens = lower.split("_")
    if "r" in tokens:
        return True
    return False


def is_bone_left_side(name):
    """
    Detects if a bone belongs to the left side of the body across naming conventions:
    - Suffixes: _l, .l, -l, _L, .L, -L
    - Prefixes: l_, l., l-, L_, L., L-
    - Words/tokens: left, Left, LEFT, _l_, .l.
    - CC5 tokens: CC_Base_L_...
    """
    if not name:
        return False
    lower = name.lower()
    for suffix in ("_l", ".l", "-l"):
        if lower.endswith(suffix):
            return True
    for prefix in ("l_", "l.", "l-"):
        if lower.startswith(prefix) or f":{prefix}" in lower:
            return True
    if "_l_" in lower or ".l." in lower or "-l-" in lower or "left" in lower:
        return True
    tokens = lower.split("_")
    if "l" in tokens:
        return True
    return False


def clean_bone_name(name):
    """Strips common namespace and rig prefixes (mixamorig:, CC_Base_, CC_, etc.) and lowers."""
    clean = name.split(":")[-1].split("|")[-1].lower()
    for prefix in ("cc_base_", "cc_"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
    return clean


def is_helper_bone(bname):
    """Identifies subsidiary, twist, share, or non-skeletal helper bones in rigs (CC5, etc.)."""
    if not bname:
        return False
    b_low = bname.lower()
    if "neck" in b_low:
        return False
    return any(skip in b_low for skip in ("twist", "share", "breast", "rib", "root", "facial", "jaw", "tongue", "teeth", "eye"))


def is_twist_bone(bname):
    """Identifies limb/auxiliary twist helper bones in CC5 and other rigs."""
    if not bname:
        return False
    b_low = bname.lower()
    if "neck" in b_low:
        return False
    return "twist" in b_low


def is_rig_cc5(arm_obj, settings=None):
    """Returns True if the target armature is a Character Creator rig."""
    if settings:
        mode = getattr(settings, "character_rig_type", "AUTO")
        if mode == "CC5":
            return True
        if mode == "CASCADEUR_MIXAMO":
            return False
    if not arm_obj or not arm_obj.pose:
        return False
    return any(b.name.startswith("CC_Base_") or b.name.startswith("CC_") for b in arm_obj.pose.bones)


_pelvis_bone_cache = {}


def get_pelvis_bone(pose_bones):
    """
    Finds the single master pelvis/hips bone in the armature that is permitted to translate.
    All other bones only rotate and never translate.
    Cached for maximum per-packet performance.
    """
    if not pose_bones:
        return None

    arm_id = id(pose_bones)
    if arm_id in _pelvis_bone_cache:
        cached_name = _pelvis_bone_cache[arm_id]
        if cached_name and cached_name in pose_bones:
            return pose_bones[cached_name]

    res = None
    # Sort pose bones: non-helper bones first, then shorter names
    for pb in sorted(pose_bones, key=lambda k: (1 if is_helper_bone(k.name) else 0, len(clean_bone_name(k.name)))):
        if is_helper_bone(pb.name):
            continue
        n_low = pb.name.lower()
        # If parent is already a hip or pelvis bone, this is a subsidiary joint (like CC_Base_Pelvis child of CC_Base_Hip)
        if pb.parent and any(k in pb.parent.name.lower() for k in ("hip", "pelvis")):
            continue
        if any(k in n_low for k in ("hip", "pelvis")):
            res = pb
            break

    if res is None:
        for pb in pose_bones:
            if pb.parent is None and not is_helper_bone(pb.name):
                res = pb
                break

    if res is None:
        res = next(iter(pose_bones.values()), None)

    _pelvis_bone_cache[arm_id] = res.name if res else None
    return res


def is_pelvis_or_root(pbone):
    """
    Identifies if a bone is the pelvis/root translation bone.
    Only this single bone is permitted to translate. All other bones only rotate.
    """
    if not pbone:
        return False
    if hasattr(pbone, "id_data") and pbone.id_data and hasattr(pbone.id_data, "pose") and pbone.id_data.pose:
        master = get_pelvis_bone(pbone.id_data.pose.bones)
        if master:
            return pbone.name == master.name

    name_lower = pbone.name.lower()
    if pbone.parent and any(k in pbone.parent.name.lower() for k in ("hip", "pelvis")):
        return False
    if any(k in name_lower for k in ("hip", "pelvis")):
        return True
    if pbone.parent is None:
        has_hip_child = any(any(k in c.name.lower() for k in ("hip", "pelvis")) for c in pbone.children)
        if not has_hip_child:
            return True
    return False


BONE_SYNONYMS = {
    "hips": ["hip", "pelvis", "root", "b_hips", "root_joint"],
    "pelvis": ["hip", "hips", "b_pelvis"],
    "stomach": ["spine", "spine_01", "b_spine", "spine_fk", "spine01"],
    "spine": ["stomach", "spine_01", "b_spine", "spine_fk", "spine01"],
    "spine1": ["spine_02", "spine2", "b_spine1", "spine02", "chest"],
    "spine2": ["chest", "spine_03", "upperchest", "b_spine2", "spine02"],
    "chest": ["spine2", "spine_02", "spine_03", "upperchest", "spine02"],
    "neck": ["neck_01", "b_neck", "neck01", "cc_base_necktwist01", "necktwist01", "neck_twist_01"],
    "neck1": ["neck_02", "neck02", "cc_base_necktwist02", "necktwist02", "neck_twist_02"],
    "head": ["head_01", "b_head"],
    # Left Arm
    "leftshoulder": ["clavicle_l", "shoulder_l", "l_shoulder", "l_clavicle", "b_leftshoulder"],
    "leftarm": ["arm_l", "left_arm", "upperarm_l", "b_leftarm", "l_upperarm"],
    "leftforearm": ["forearm_l", "lowerarm_l", "b_leftforearm", "l_lowerarm", "left_forearm", "l_forearm"],
    "lefthand": ["hand_l", "b_lefthand", "l_hand", "left_hand"],
    # Right Arm
    "rightshoulder": ["clavicle_r", "shoulder_r", "r_shoulder", "r_clavicle", "b_rightshoulder"],
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
    "leftthumb1": ["thumb_01_l", "l_thumb1", "lefthandthumb1"],
    "leftthumb2": ["thumb_02_l", "l_thumb2", "lefthandthumb2"],
    "leftthumb3": ["thumb_03_l", "l_thumb3", "lefthandthumb3"],
    "leftindex1": ["index_01_l", "l_index1", "lefthandindex1"],
    "leftindex2": ["index_02_l", "l_index2", "lefthandindex2"],
    "leftindex3": ["index_03_l", "l_index3", "lefthandindex3"],
    "leftmiddle1": ["middle_01_l", "l_mid1", "l_middle1", "lefthandmiddle1"],
    "leftmiddle2": ["middle_02_l", "l_mid2", "l_middle2", "lefthandmiddle2"],
    "leftmiddle3": ["middle_03_l", "l_mid3", "l_middle3", "lefthandmiddle3"],
    "leftring1": ["ring_01_l", "l_ring1", "lefthandring1"],
    "leftring2": ["ring_02_l", "l_ring2", "lefthandring2"],
    "leftring3": ["ring_03_l", "l_ring3", "lefthandring3"],
    "leftpinky1": ["pinky_01_l", "l_pinky1", "little_01_l", "lefthandpinky1"],
    "leftpinky2": ["pinky_02_l", "l_pinky2", "little_02_l", "lefthandpinky2"],
    "leftpinky3": ["pinky_03_l", "l_pinky3", "little_03_l", "lefthandpinky3"],
    # Right Fingers
    "rightthumb1": ["thumb_01_r", "r_thumb1", "righthandthumb1"],
    "rightthumb2": ["thumb_02_r", "r_thumb2", "righthandthumb2"],
    "rightthumb3": ["thumb_03_r", "r_thumb3", "righthandthumb3"],
    "rightindex1": ["index_01_r", "r_index1", "righthandindex1"],
    "rightindex2": ["index_02_r", "r_index2", "righthandindex2"],
    "rightindex3": ["index_03_r", "r_index3", "righthandindex3"],
    "rightmiddle1": ["middle_01_r", "r_mid1", "r_middle1", "righthandmiddle1"],
    "rightmiddle2": ["middle_02_r", "r_mid2", "r_middle2", "righthandmiddle2"],
    "rightmiddle3": ["middle_03_r", "r_mid3", "r_middle3", "righthandmiddle3"],
    "rightring1": ["ring_01_r", "r_ring1", "righthandring1"],
    "rightring2": ["ring_02_r", "r_ring2", "righthandring2"],
    "rightring3": ["ring_03_r", "r_ring3", "righthandring3"],
    "rightpinky1": ["pinky_01_r", "r_pinky1", "little_01_r", "righthandpinky1"],
    "rightpinky2": ["pinky_02_r", "r_pinky2", "little_02_r", "righthandpinky2"],
    "rightpinky3": ["pinky_03_r", "r_pinky3", "little_03_r", "righthandpinky3"],
}

# Runtime caches for maximum viewport performance
_bone_match_cache = {}
_armature_depth_cache = {}


def find_matching_bone(pose_bones, incoming_name, prefix="", smart_map=True, is_cc5=False):
    """Attempt to match an incoming Cascadeur joint name to a Blender pose bone with O(1) caching."""
    settings = getattr(bpy.context.scene, 'cascadeur_live_link', None)
    if settings and settings.target_armature and pose_bones.id_data == settings.target_armature:
        for row in settings.bone_mapping:
            if row.source == incoming_name:
                return pose_bones.get(row.target) if row.enabled else None
        if settings.mapping_only:
            return None
    if is_cc5 and is_twist_bone(incoming_name):
        return None

    cache_key = (pose_bones.id_data.as_pointer(), incoming_name, prefix, smart_map, is_cc5)
    if cache_key in _bone_match_cache:
        cached_name = _bone_match_cache[cache_key]
        if cached_name is None:
            return None
        return pose_bones.get(cached_name)

    def valid_bone(pb):
        if not pb:
            return None
        if is_cc5 and is_twist_bone(pb.name):
            return None
        return pb

    def cache_and_return(res):
        _bone_match_cache[cache_key] = res.name if res else None
        return res

    # 1. Direct match
    if incoming_name in pose_bones:
        res = valid_bone(pose_bones[incoming_name])
        if res:
            return cache_and_return(res)

    # 2. Prefix-stripped match
    if prefix and incoming_name.startswith(prefix):
        stripped = incoming_name[len(prefix):]
        if stripped in pose_bones:
            res = valid_bone(pose_bones[stripped])
            if res:
                return cache_and_return(res)

    # 3. Strip namespaces (e.g. "mixamorig:Hips" -> "Hips", "Character1:Spine" -> "Spine")
    clean_incoming = incoming_name.split(":")[-1].split("|")[-1]
    if clean_incoming in pose_bones:
        res = valid_bone(pose_bones[clean_incoming])
        if res:
            return cache_and_return(res)

    if not smart_map:
        return cache_and_return(None)

    clean_incoming_lower = clean_bone_name(incoming_name)

    # Special case for CC rigs: the anatomical root of the body is CC_Base_Hip (which parents both Pelvis and Waist)
    if clean_incoming_lower in ("pelvis", "hips", "hip"):
        for pb_name in ("CC_Base_Hip", "cc_base_hip"):
            if pb_name in pose_bones:
                res = valid_bone(pose_bones[pb_name])
                if res:
                    return cache_and_return(res)

    # 4. Search Blender pose bones for clean name match (ignoring namespaces and casing)
    for pb_name, pb in pose_bones.items():
        if is_cc5 and is_twist_bone(pb_name):
            continue
        clean_pb = clean_bone_name(pb_name)
        if clean_pb == clean_incoming_lower:
            return cache_and_return(pb)

    # 5. Bidirectional Synonym Mapping (e.g. Cascy 'arm_r' <-> Mixamo 'RightArm' <-> 'upperarm_r')
    clean_incoming_norm = clean_incoming_lower.replace("_", "").replace(".", "").replace("-", "")
    for canon, aliases in BONE_SYNONYMS.items():
        all_group = [canon] + aliases
        all_group_clean = [a.lower().replace("_", "").replace(".", "").replace("-", "") for a in all_group]
        if clean_incoming_lower in all_group or clean_incoming_norm in all_group_clean:
            # Sort pose bones: non-helper bones first, then shorter names / exact matches
            for pb_name in sorted(pose_bones.keys(), key=lambda k: (1 if is_helper_bone(k) else 0, len(clean_bone_name(k)), k)):
                if is_cc5 and is_twist_bone(pb_name):
                    continue
                pb = pose_bones[pb_name]
                pb_clean = clean_bone_name(pb_name)
                pb_norm = pb_clean.replace("_", "").replace(".", "").replace("-", "")
                if pb_clean in all_group or pb_norm in all_group_clean:
                    return cache_and_return(pb)

    return cache_and_return(None)


def calculate_translation_scale(packet, arm_obj, settings):
    """
    Compares the distance between landmark joints (e.g. Hips to Head/Neck/Spine)
    in Cascadeur vs Blender to automatically determine the exact translation scale ratio.
    """
    bones_data = packet.get("bones", {})
    if not arm_obj or not arm_obj.pose or not bones_data:
        return 1.0

    pose_bones = arm_obj.pose.bones
    prefix = settings.bone_name_prefix.strip()
    smart_map = settings.auto_map_synonyms

    # Find Hips/Pelvis in Blender (prefer non-helper, primary hips/pelvis bone)
    hips_pbone = get_pelvis_bone(pose_bones)
    if not hips_pbone:
        return 1.0

    # Find matching Hips in Cascadeur
    hips_tf = None
    for j_name, tf in bones_data.items():
        matched = find_matching_bone(pose_bones, j_name, prefix, smart_map=smart_map)
        if matched and matched.name == hips_pbone.name:
            hips_tf = tf
            break

    if not hips_tf:
        return 1.0

    hips_c = Vector(hips_tf.get("g_pos") or hips_tf.get("pos") or [0, 0, 0])
    hips_b = hips_pbone.bone.head_local

    # Candidates in order of priority: Head -> Neck -> Spine -> Foot -> Arm
    candidates = ["head", "neck", "spine", "chest", "foot", "leg", "arm"]
    best_scale = None
    max_c_dist = 0.0

    for candidate_kw in candidates:
        for pb in pose_bones:
            if pb.name == hips_pbone.name:
                continue
            if candidate_kw in pb.name.lower():
                for j_name, tf in bones_data.items():
                    matched = find_matching_bone(pose_bones, j_name, prefix, smart_map=smart_map)
                    if matched and matched.name == pb.name:
                        c_pos = Vector(tf.get("g_pos") or tf.get("pos") or [0, 0, 0])
                        d_casc = (c_pos - hips_c).length
                        d_blender_world = (arm_obj.matrix_world @ pb.bone.head_local - arm_obj.matrix_world @ hips_b).length
                        if d_casc > 0.05 and d_blender_world > 0.0005 and d_casc > max_c_dist:
                            max_c_dist = d_casc
                            best_scale = d_blender_world / d_casc
        if best_scale is not None and max_c_dist > 0.2:
            break

    if best_scale is not None and best_scale > 0.0:
        return best_scale
    return 1.0


# Runtime rest pose calibration cache:
# Maps bone_name -> (q_casc_rest, q_blender_rest)
_rest_pose_cache = {}
_rest_pose_local_cache = {}
_rest_pose_root_casc_pos = None
_rest_pose_root_casc_pos_local = None
_initial_root_pos = None


def calibrate_rest_pose(packet, arm_obj, settings):
    """
    Compares the rest/bind pose from Cascadeur (in packet) with the target Blender
    armature's native rest pose (bone.matrix_local) and stores reference orientations.
    Stores offsets in _rest_pose_cache, _rest_pose_local_cache, and settings.rest_pose_data.
    """
    global _rest_pose_cache, _rest_pose_local_cache, _rest_pose_root_casc_pos, _rest_pose_root_casc_pos_local, _initial_root_pos
    _initial_root_pos = None
    _rest_pose_root_casc_pos = None
    _rest_pose_root_casc_pos_local = None

    if not arm_obj or not arm_obj.pose or not packet:
        return 0

    pose_bones = arm_obj.pose.bones
    bones_data = packet.get("bones", {})
    if not bones_data:
        return 0

    prefix = settings.bone_name_prefix.strip()
    smart_map = settings.auto_map_synonyms

    calibrated_count = 0
    serialized_cache = {}

    for bone_name, transform in bones_data.items():
        pbone = find_matching_bone(pose_bones, bone_name, prefix, smart_map=smart_map)
        if not pbone:
            continue

        raw_rot = transform.get("g_rot") or transform.get("rot")
        if not raw_rot or len(raw_rot) != 4:
            continue

        # Cascadeur global rest orientation in Cascadeur frame (which matches Armature local frame)
        q_casc_rest = Quaternion(raw_rot).normalized()

        # Cascadeur local rest orientation
        raw_local_rot = transform.get("rot")
        if raw_local_rot and len(raw_local_rot) == 4:
            q_casc_rest_local = Quaternion(raw_local_rot).normalized()
        else:
            q_casc_rest_local = q_casc_rest.copy()

        # Blender rest orientation in Armature Object space
        q_blender_rest = pbone.bone.matrix_local.to_quaternion()

        _rest_pose_cache[pbone.name] = (q_casc_rest.copy(), q_blender_rest.copy())
        _rest_pose_local_cache[pbone.name] = q_casc_rest_local.copy()
        serialized_cache[pbone.name] = {
            "q_casc": [q_casc_rest.w, q_casc_rest.x, q_casc_rest.y, q_casc_rest.z],
            "q_blender": [q_blender_rest.w, q_blender_rest.x, q_blender_rest.y, q_blender_rest.z],
            "q_casc_local": [q_casc_rest_local.w, q_casc_rest_local.x, q_casc_rest_local.y, q_casc_rest_local.z],
        }
        calibrated_count += 1

    # Record reference root joint position for zero-drift grounding
    hips_pbone = get_pelvis_bone(pose_bones)

    if hips_pbone:
        for j_name, tf in bones_data.items():
            matched = find_matching_bone(pose_bones, j_name, prefix, smart_map=smart_map)
            if matched and matched.name == hips_pbone.name:
                raw_pos = tf.get("g_pos") or tf.get("pos")
                if raw_pos and len(raw_pos) >= 3:
                    scale = getattr(settings, "translation_scale", 0.01)
                    coord_mode = getattr(settings, "coord_conversion", "Y_UP_TO_Z_UP")
                    world_to_arm = arm_obj.matrix_world.inverted()
                    if coord_mode == "Y_UP_TO_Z_UP":
                        v_world = Vector((raw_pos[0], -raw_pos[2], raw_pos[1])) * scale
                    else:
                        v_world = Vector((raw_pos[0], raw_pos[1], raw_pos[2])) * scale
                    _rest_pose_root_casc_pos = world_to_arm @ v_world
                    serialized_cache["__root_casc_pos__"] = [
                        _rest_pose_root_casc_pos.x,
                        _rest_pose_root_casc_pos.y,
                        _rest_pose_root_casc_pos.z,
                    ]
                raw_local_pos = tf.get("pos")
                if raw_local_pos and len(raw_local_pos) >= 3:
                    _rest_pose_root_casc_pos_local = Vector(raw_local_pos)
                    serialized_cache["__root_casc_pos_local__"] = raw_local_pos
                break

    if calibrated_count > 0:
        settings.is_rest_pose_calibrated = True
        settings.calibrated_bone_count = calibrated_count
        try:
            settings.rest_pose_data = json.dumps(serialized_cache)
        except Exception:
            pass

    return calibrated_count


def ensure_rest_pose_cache(settings, arm_obj):
    """Restores _rest_pose_cache and _rest_pose_local_cache from serialized settings.rest_pose_data if empty."""
    global _rest_pose_cache, _rest_pose_local_cache, _rest_pose_root_casc_pos, _rest_pose_root_casc_pos_local
    if _rest_pose_cache:
        return True
    if not getattr(settings, "rest_pose_data", ""):
        return False
    try:
        data = json.loads(settings.rest_pose_data)
        for bname, qdata in data.items():
            if bname.startswith("__"):
                continue
            qc = Quaternion(qdata["q_casc"])
            qb = Quaternion(qdata["q_blender"])
            qcl_data = qdata.get("q_casc_local")
            qcl = Quaternion(qcl_data) if qcl_data else qc.copy()
            _rest_pose_cache[bname] = (qc, qb)
            _rest_pose_local_cache[bname] = qcl
        if "__root_casc_pos__" in data:
            _rest_pose_root_casc_pos = Vector(data["__root_casc_pos__"])
        if "__root_casc_pos_local__" in data:
            _rest_pose_root_casc_pos_local = Vector(data["__root_casc_pos_local__"])
        return len(_rest_pose_cache) > 0
    except Exception:
        return False


def apply_packet_to_rig(packet, arm_obj, settings, record, sync_frame, context):
    """Applies a parsed animation packet to target pose bones with smart matching."""
    global _last_frame_applied, _last_packet_received, _scale_auto_detected
    _last_packet_received = packet

    if not arm_obj or not arm_obj.pose:
        return

    # Auto-detect translation scale on incoming packet if enabled
    if getattr(settings, "auto_calc_scale", True) and not _scale_auto_detected:
        detected = calculate_translation_scale(packet, arm_obj, settings)
        if detected and abs(detected - 1.0) > 1e-4:
            settings.translation_scale = detected
            _scale_auto_detected = True

    # Intercept dedicated rest_pose packet for calibration
    if packet.get("type") == "rest_pose":
        calibrate_rest_pose(packet, arm_obj, settings)

    # Ensure memory cache is populated if settings indicate calibrated
    if getattr(settings, "use_rest_pose_calibration", True) and settings.is_rest_pose_calibrated:
        ensure_rest_pose_cache(settings, arm_obj)

    # Ensure armature is evaluating pose transforms
    if arm_obj.data.pose_position != 'POSE':
        arm_obj.data.pose_position = 'POSE'

    pose_bones = arm_obj.pose.bones
    bones_data = packet.get("bones", {})
    frame_num = int(packet.get("frame", context.scene.frame_current))

    coord_mode = settings.coord_conversion
    prefix = settings.bone_name_prefix.strip()
    smart_map = settings.auto_map_synonyms
    root_only_trans = settings.apply_root_only_translation
    mapping_mode = getattr(settings, "pose_mapping_mode", "GLOBAL_AIM")

    # Only call frame_set when frame actually changes (very expensive - full depsgraph eval)
    if (sync_frame or record) and context.scene.frame_current != frame_num:
        context.scene.frame_set(frame_num)
        _last_frame_applied = frame_num

    matched_bones = {}
    matched_names = []
    unmatched_names = []

    is_cc5 = is_rig_cc5(arm_obj, settings)

    for bone_name, transform in bones_data.items():
        if is_cc5 and is_twist_bone(bone_name):
            continue
        pbone = find_matching_bone(pose_bones, bone_name, prefix, smart_map=smart_map, is_cc5=is_cc5)
        if not pbone:
            unmatched_names.append(bone_name)
            continue
        if is_cc5 and is_twist_bone(pbone.name):
            continue
        matched_bones[pbone] = transform
        matched_names.append(pbone.name)

    # Check if incoming packet has 3D global position data for GLOBAL_AIM
    has_gpos = any("g_pos" in tf for tf in matched_bones.values())

    if mapping_mode in {"GLOBAL_DELTA", "GLOBAL_MATRIX"}:
        from .keyframe_bridge import commit_pose
        commit_pose(packet, arm_obj, settings, context, record=record, sync_frame=sync_frame)
    elif mapping_mode == "GLOBAL_AIM" and has_gpos:
        _apply_global_aim(matched_bones, arm_obj, settings, record, coord_mode, root_only_trans)
    else:
        _apply_local_fk(matched_bones, arm_obj, settings, record, coord_mode, root_only_trans)

    if is_cc5:
        # Keep all twist bones on the CC5 character strictly in neutral rest pose (zero local rotation & translation)
        # so they never deform or distort the mesh
        for pb in arm_obj.pose.bones:
            if is_twist_bone(pb.name):
                pb.rotation_mode = 'QUATERNION'
                pb.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
                pb.location = Vector((0.0, 0.0, 0.0))

    # Update diagnostic stats on settings
    settings.matched_bones_count = len(matched_names)
    settings.total_incoming_bones = len(bones_data)
    settings.last_matched_bone_names = ", ".join(matched_names[:6])
    settings.last_unmatched_bone_names = ", ".join(unmatched_names[:6])

    # Tag armature for viewport update (single batched refresh after all bones applied)
    arm_obj.update_tag(refresh={'OBJECT', 'DATA'})


def _apply_global_aim(matched_bones, arm_obj, settings, record, coord_mode, root_only_trans):
    """
    Solves bone rotations in Blender directly from Cascadeur's 3D global joint positions and orientations.
    Each bone aims from its global position to its child joint's global position.
    Only the pelvis/root bone translates; all child bones only rotate and never detach.
    """
    world_to_arm = arm_obj.matrix_world.inverted()
    scale = getattr(settings, "translation_scale", 1.0)
    mapping_mode = getattr(settings, "pose_mapping_mode", "GLOBAL_AIM")
    is_cc5 = is_rig_cc5(arm_obj, settings)
    if is_cc5 and scale == 1.0:
        scale = 0.01
    elif not is_cc5 and scale == 0.01:
        pelvis_pb = get_pelvis_bone(arm_obj.pose.bones)
        if pelvis_pb and (abs(pelvis_pb.head.y) > 10.0 or abs(pelvis_pb.head.z) > 10.0):
            scale = 1.0

    arm_positions = {}
    arm_rotations = {}
    arm_delta_rotations = {}

    for pbone, tf in matched_bones.items():
        g_pos = tf.get("g_pos") or tf.get("pos")
        if g_pos and len(g_pos) >= 3:
            if coord_mode == "Y_UP_TO_Z_UP":
                # Cascadeur Y-Up (cm) -> Blender World Z-Up (m)
                v_world = Vector((g_pos[0], -g_pos[2], g_pos[1])) * scale
            else:
                v_world = Vector((g_pos[0], g_pos[1], g_pos[2])) * scale
            arm_positions[pbone.name] = world_to_arm @ v_world

        g_rot = tf.get("g_rot") or tf.get("rot")
        if g_rot and len(g_rot) == 4:
            arm_rotations[pbone.name] = g_rot

        d_rot = tf.get("delta_rot")
        if d_rot and len(d_rot) == 4:
            arm_delta_rotations[pbone.name] = d_rot

    # Cached topological depth lookup
    arm_name = arm_obj.name
    depth_lookup = _armature_depth_cache.get(arm_name)
    if depth_lookup is None:
        depth_lookup = {}
        for pb in arm_obj.pose.bones:
            d = 0
            cur = pb
            while cur.parent:
                d += 1
                cur = cur.parent
            depth_lookup[pb.name] = d
        _armature_depth_cache[arm_name] = depth_lookup

    # Group by hierarchy depth
    depth_map = {}
    for pb in matched_bones.keys():
        d = depth_lookup.get(pb.name, 0)
        depth_map.setdefault(d, []).append(pb)

    use_calib_global = getattr(settings, "use_rest_pose_calibration", True) and settings.is_rest_pose_calibrated
    solved_armature_rots = {}

    for depth in sorted(depth_map.keys()):
        for pbone in depth_map[depth]:
            name = pbone.name
            if name not in arm_positions and name not in arm_rotations and name not in arm_delta_rotations:
                continue

            p_head = arm_positions.get(name, pbone.head)
            is_root = is_pelvis_or_root(pbone)

            # Find child joint target position in arm_positions
            p_child = None
            p_child_bone = None
            lower = name.lower()

            # Prefer child along the same anatomical branch (excluding subsidiary/twist helper bones)
            matched_children = [c for c in pbone.children if c.name in arm_positions and not is_helper_bone(c.name)]
            if len(matched_children) == 1:
                p_child_bone = matched_children[0]
                p_child = arm_positions[p_child_bone.name]
            elif len(matched_children) > 1:
                # If torso (hips, spine, chest), aim towards upper spine/neck rather than limbs
                if any(k in lower for k in ("hip", "pelvis", "spine", "chest", "torso")):
                    p_child_bone = next((c for c in matched_children if any(k in c.name.lower() for k in ("spine", "chest", "neck", "head"))), None)
                elif any(k in lower for k in ("shoulder", "clavicle", "collar")):
                    p_child_bone = next((c for c in matched_children if "arm" in c.name.lower()), None)
                elif any(k in lower for k in ("arm", "upperarm")) and not any(k in lower for k in ("fore", "low", "hand")):
                    p_child_bone = next((c for c in matched_children if any(k in c.name.lower() for k in ("forearm", "lowerarm", "hand"))), None)
                elif any(k in lower for k in ("forearm", "lowerarm")):
                    p_child_bone = next((c for c in matched_children if "hand" in c.name.lower()), None)
                elif any(k in lower for k in ("thigh", "upleg", "leg")) and not any(k in lower for k in ("calf", "shin", "foot", "toe")):
                    p_child_bone = next((c for c in matched_children if any(k in c.name.lower() for k in ("calf", "shin", "foot"))), None)
                elif any(k in lower for k in ("calf", "shin")):
                    p_child_bone = next((c for c in matched_children if "foot" in c.name.lower()), None)

                if p_child_bone is None:
                    p_child_bone = matched_children[0]
                p_child = arm_positions[p_child_bone.name]

            # Fallback continuation for standard naming across conventions (Cascy _r/_l, Mixamo Right/Left)
            if p_child is None:
                is_r = is_bone_right_side(name)
                is_l = is_bone_left_side(name)
                child_candidate_name = None

                if is_r:
                    if any(k in lower for k in ("arm", "upperarm", "shoulder", "clavicle")) and not any(k in lower for k in ("fore", "low", "hand")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_right_side(n) and any(k in n.lower() for k in ("forearm", "lowerarm", "hand"))), None)
                    elif any(k in lower for k in ("forearm", "lowerarm")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_right_side(n) and "hand" in n.lower()), None)
                    elif any(k in lower for k in ("thigh", "upleg", "leg")) and not any(k in lower for k in ("calf", "shin", "foot", "toe")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_right_side(n) and any(k in n.lower() for k in ("calf", "shin", "foot"))), None)
                    elif any(k in lower for k in ("calf", "shin")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_right_side(n) and "foot" in n.lower()), None)
                    elif "foot" in lower:
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_right_side(n) and "toe" in n.lower()), None)
                elif is_l:
                    if any(k in lower for k in ("arm", "upperarm", "shoulder", "clavicle")) and not any(k in lower for k in ("fore", "low", "hand")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_left_side(n) and any(k in n.lower() for k in ("forearm", "lowerarm", "hand"))), None)
                    elif any(k in lower for k in ("forearm", "lowerarm")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_left_side(n) and "hand" in n.lower()), None)
                    elif any(k in lower for k in ("thigh", "upleg", "leg")) and not any(k in lower for k in ("calf", "shin", "foot", "toe")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_left_side(n) and any(k in n.lower() for k in ("calf", "shin", "foot"))), None)
                    elif any(k in lower for k in ("calf", "shin")):
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_left_side(n) and "foot" in n.lower()), None)
                    elif "foot" in lower:
                        child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and is_bone_left_side(n) and "toe" in n.lower()), None)
                elif "neck" in lower:
                    child_candidate_name = next((n for n in arm_positions if not is_helper_bone(n) and "head" in n.lower()), None)

                if child_candidate_name:
                    p_child = arm_positions[child_candidate_name]
                    p_child_bone = arm_obj.pose.bones.get(child_candidate_name)

            pbone.rotation_mode = 'QUATERNION'
            has_delta = name in arm_delta_rotations
            has_calib = use_calib_global and (name in _rest_pose_cache)

            if is_root:
                trans_mode = getattr(settings, "translation_mode", "ABSOLUTE")
                if trans_mode == "RELATIVE" or use_calib_global:
                    global _initial_root_pos
                    ref_root = _rest_pose_root_casc_pos if (use_calib_global and _rest_pose_root_casc_pos is not None) else _initial_root_pos
                    if ref_root is None:
                        _initial_root_pos = p_head.copy()
                        ref_root = _initial_root_pos
                    delta_p = (p_head - ref_root)
                    scaled_head = pbone.bone.head_local + delta_p
                else:
                    scaled_head = p_head

                # Pelvis/Hips translates in world space
                if has_delta:
                    q_d = Quaternion(arm_delta_rotations[name]).normalized()
                    if is_cc5:
                        m_conv = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
                        r_b = m_conv.transposed() @ q_d.to_matrix() @ m_conv
                        target_mat_3x3 = r_b @ pbone.bone.matrix_local.to_3x3()
                    else:
                        target_mat_3x3 = q_d.to_matrix() @ pbone.bone.matrix_local.to_3x3()
                    pbone.matrix = Matrix.Translation(scaled_head) @ target_mat_3x3.to_4x4()
                    solved_armature_rots[name] = target_mat_3x3
                elif has_calib:
                    q_casc_rest, q_blender_rest = _rest_pose_cache[name][:2]
                    g_rot = arm_rotations.get(name)
                    if g_rot:
                        q_casc_curr = Quaternion(g_rot).normalized()
                        delta_q = (q_casc_curr @ q_casc_rest.inverted()).normalized()
                        q_target = (delta_q @ q_blender_rest).normalized()
                        pbone.matrix = Matrix.Translation(scaled_head) @ q_target.to_matrix().to_4x4()
                        solved_armature_rots[name] = q_target.to_matrix()
                    else:
                        pbone.matrix = Matrix.Translation(scaled_head) @ q_blender_rest.to_matrix().to_4x4()
                        solved_armature_rots[name] = q_blender_rest.to_matrix()
                elif name in arm_rotations:
                    g_rot = arm_rotations[name]
                    q_curr = Quaternion(g_rot).normalized()
                    pbone.matrix = Matrix.Translation(scaled_head) @ q_curr.to_matrix().to_4x4()
                    solved_armature_rots[name] = q_curr.to_matrix()
                else:
                    pbone.matrix = Matrix.Translation(scaled_head)
                    solved_armature_rots[name] = pbone.bone.matrix_local.to_3x3()

                if record:
                    pbone.keyframe_insert(data_path="location")
                    pbone.keyframe_insert(data_path="rotation_quaternion")
            else:
                # CHILD BONES: ONLY ROTATE! NEVER TRANSLATE! Head stays anchored to parent joint in Blender!
                if not is_cc5:
                    # CASCEUR / MIXAMO (1:1 3D rotation preservation)
                    if has_delta:
                        q_d = Quaternion(arm_delta_rotations[name]).normalized()
                        target_rot_3x3 = q_d.to_matrix() @ pbone.bone.matrix_local.to_3x3()
                    elif has_calib:
                        q_casc_rest, q_blender_rest = _rest_pose_cache[name][:2]
                        g_rot = arm_rotations.get(name)
                        if g_rot:
                            q_casc_curr = Quaternion(g_rot).normalized()
                            delta_q = (q_casc_curr @ q_casc_rest.inverted()).normalized()
                            target_rot_3x3 = (delta_q @ q_blender_rest).to_matrix()
                        else:
                            target_rot_3x3 = q_blender_rest.to_matrix()
                    elif name in arm_rotations:
                        g_rot = arm_rotations[name]
                        q_curr = Quaternion(g_rot).normalized()
                        target_rot_3x3 = q_curr.to_matrix()
                    elif p_child is not None and (p_child - p_head).length > 1e-4:
                        target_dir = (p_child - p_head).normalized()
                        if p_child_bone is not None:
                            rest_dir = (p_child_bone.bone.head_local - pbone.bone.head_local).normalized()
                        else:
                            rest_dir = (pbone.bone.tail_local - pbone.bone.head_local).normalized()
                        rot_diff = rest_dir.rotation_difference(target_dir)
                        target_rot_3x3 = (rot_diff.to_matrix().to_4x4() @ pbone.bone.matrix_local).to_3x3()
                    else:
                        target_rot_3x3 = pbone.bone.matrix_local.to_3x3()
                else:
                    # CC5 / CHARACTER CREATOR:
                    # Use exact 3D delta rotation if bone was actively rotated (preserves 1:1 orientation like Cascy)
                    has_real_delta = has_delta and (abs(Quaternion(arm_delta_rotations[name]).angle) > 1e-3)
                    if has_real_delta:
                        q_d = Quaternion(arm_delta_rotations[name]).normalized()
                        m_conv = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
                        r_b = m_conv.transposed() @ q_d.to_matrix() @ m_conv
                        target_rot_3x3 = (r_b @ pbone.bone.matrix_local.to_3x3()).to_3x3()
                    elif p_child is not None and (p_child - p_head).length > 1e-4:
                        target_dir = (p_child - p_head).normalized()
                        if p_child_bone is not None:
                            rest_dir = (p_child_bone.bone.head_local - pbone.bone.head_local).normalized()
                        else:
                            rest_dir = (pbone.bone.tail_local - pbone.bone.head_local).normalized()
                        rot_diff = rest_dir.rotation_difference(target_dir)
                        target_rot_3x3 = (rot_diff.to_matrix().to_4x4() @ pbone.bone.matrix_local).to_3x3()
                    elif has_delta:
                        q_d = Quaternion(arm_delta_rotations[name]).normalized()
                        m_conv = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
                        r_b = m_conv.transposed() @ q_d.to_matrix() @ m_conv
                        target_rot_3x3 = (r_b @ pbone.bone.matrix_local.to_3x3()).to_3x3()
                    elif has_calib:
                        q_casc_rest, q_blender_rest = _rest_pose_cache[name][:2]
                        g_rot = arm_rotations.get(name)
                        if g_rot:
                            q_casc_curr = Quaternion(g_rot).normalized()
                            delta_q = (q_casc_curr @ q_casc_rest.inverted()).normalized()
                            target_rot_3x3 = (delta_q @ q_blender_rest).to_matrix()
                        else:
                            target_rot_3x3 = q_blender_rest.to_matrix()
                    elif name in arm_rotations:
                        g_rot = arm_rotations[name]
                        q_curr = Quaternion(g_rot).normalized()
                        m_conv = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
                        r_b = m_conv.transposed() @ q_curr.to_matrix() @ m_conv
                        target_rot_3x3 = r_b
                    else:
                        target_rot_3x3 = pbone.bone.matrix_local.to_3x3()

                solved_armature_rots[name] = target_rot_3x3

                # Convert armature target rotation into child bone's parent-relative local space
                if pbone.parent and pbone.parent.name in solved_armature_rots:
                    parent_pose_rot = solved_armature_rots[pbone.parent.name]
                    rest_in_parent = (pbone.parent.bone.matrix_local.inverted() @ pbone.bone.matrix_local).to_3x3()
                    local_rot_3x3 = rest_in_parent.inverted() @ parent_pose_rot.inverted() @ target_rot_3x3
                elif pbone.parent:
                    parent_pose_rot = pbone.parent.matrix.to_3x3()
                    rest_in_parent = (pbone.parent.bone.matrix_local.inverted() @ pbone.bone.matrix_local).to_3x3()
                    local_rot_3x3 = (parent_pose_rot @ rest_in_parent).inverted() @ target_rot_3x3
                else:
                    local_rot_3x3 = pbone.bone.matrix_local.to_3x3().inverted() @ target_rot_3x3

                pbone.rotation_quaternion = local_rot_3x3.to_quaternion()
                pbone.location = Vector((0.0, 0.0, 0.0))

                if record:
                    pbone.keyframe_insert(data_path="rotation_quaternion")

        # Update view layer between depth tiers ONLY for CC5 rigs (needed for twist bone chain evaluation)
        if is_cc5 and hasattr(bpy.context, "view_layer") and bpy.context.view_layer:
            try:
                bpy.context.view_layer.update()
            except Exception:
                pass


def _apply_local_fk(matched_bones, arm_obj, settings, record, coord_mode, root_only_trans):
    """
    Applies pure local FK transforms to pose bones.
    Only the pelvis/root translates. All other bones only rotate and never detach.
    """
    for pbone, transform in matched_bones.items():
        pos_data = transform.get("pos")
        rot_data = transform.get("rot")
        is_root = is_pelvis_or_root(pbone)
        is_right = is_bone_right_side(pbone.name)

        if rot_data:
            pbone.rotation_mode = 'QUATERNION'
            use_calib = getattr(settings, "use_rest_pose_calibration", True) and settings.is_rest_pose_calibrated and (pbone.name in _rest_pose_cache)
            if use_calib:
                q_casc_rest = _rest_pose_cache[pbone.name][0]
                q_curr = Quaternion(rot_data).normalized()
                delta_q = (q_curr @ q_casc_rest.inverted()).normalized()
                pbone.rotation_quaternion = delta_q
            else:
                _, quat = convert_transforms(
                    pos_data or [0, 0, 0], rot_data, coord_mode, is_root=is_root, settings=settings, is_right=is_right, bone_name=pbone.name
                )
                pbone.rotation_quaternion = quat
            if record:
                pbone.keyframe_insert(data_path="rotation_quaternion")

        if is_root and pos_data:
            scale = getattr(settings, "translation_scale", 1.0)
            is_cc5_fk = is_rig_cc5(arm_obj, settings)
            if is_cc5_fk and scale == 1.0:
                scale = 0.01
            elif not is_cc5_fk and scale == 0.01:
                pelvis_pb = get_pelvis_bone(arm_obj.pose.bones)
                if pelvis_pb and (abs(pelvis_pb.head.y) > 10.0 or abs(pelvis_pb.head.z) > 10.0):
                    scale = 1.0
            trans_mode = getattr(settings, "translation_mode", "ABSOLUTE")
            pos, _ = convert_transforms(
                pos_data, rot_data or [1, 0, 0, 0], coord_mode, is_root=True, settings=settings, bone_name=pbone.name
            )
            if trans_mode == "RELATIVE" or use_calib:
                global _initial_root_pos
                ref_p = _rest_pose_root_casc_pos_local if (use_calib and _rest_pose_root_casc_pos_local is not None) else _initial_root_pos
                if ref_p is None:
                    _initial_root_pos = pos.copy()
                    ref_p = _initial_root_pos
                delta_p = (pos - ref_p) * scale
                pbone.location = delta_p
            else:
                pbone.location = pos * scale

            if record:
                pbone.keyframe_insert(data_path="location")
        else:
            # Child bones stay locked to their rest position relative to parent
            pbone.location = Vector((0.0, 0.0, 0.0))


def _tick_process_packets():
    """
    bpy.app.timers callback — runs on Blender's main thread at high frequency.
    This is more reliable than a modal TIMER event during active viewport interaction.
    Returns the interval in seconds to schedule the next call, or None to unregister.
    """
    global _timer_registered, _last_preview
    started = time.perf_counter()

    if _stop_event.is_set():
        _timer_registered = False
        return None  # Unregister timer

    context = bpy.context
    if not context or not hasattr(context, "scene") or not context.scene:
        return 1.0 / 60.0

    settings = getattr(context.scene, "cascadeur_live_link", None)

    if _packet_queue.empty():
        now = time.time()
        while _packet_arrival_times and _packet_arrival_times[0] < now - 1.0:
            _packet_arrival_times.popleft()
        if settings:
            if len(_packet_arrival_times) > 1:
                span = now - _packet_arrival_times[0]
                settings.stream_fps = round(len(_packet_arrival_times) / max(span, 0.001), 1)
            elif not _packet_arrival_times:
                settings.stream_fps = 0.0
        return 1.0 / 60.0  # Nothing to process, check again at 60Hz

    # Drain queue
    drained_packets = []
    packets_processed = 0
    while not _packet_queue.empty():
        try:
            pkt = _packet_queue.get_nowait()
            drained_packets.append(pkt)
            packets_processed += 1
        except queue.Empty:
            break

    if not drained_packets:
        return 1.0 / 60.0

    pose_packets = [p for p in drained_packets if p.get('bones') and 'frame' in p]
    key_messages = [p for p in drained_packets if p.get('type') == 'key_sync']
    latest_packet = pose_packets[-1] if pose_packets else {}

    if not settings:
        return 1.0 / 60.0

    settings.packets_received += packets_processed

    # Calculate real-time receiving stream FPS
    now = time.time()
    while _packet_arrival_times and _packet_arrival_times[0] < now - 1.0:
        _packet_arrival_times.popleft()
    if len(_packet_arrival_times) > 1:
        span = now - _packet_arrival_times[0]
        settings.stream_fps = round(len(_packet_arrival_times) / max(span, 0.001), 1)
    elif _packet_arrival_times:
        settings.stream_fps = float(len(_packet_arrival_times))
    else:
        settings.stream_fps = 0.0

    if "frame" in latest_packet:
        settings.last_frame_received = int(latest_packet["frame"])

    arm_obj = settings.target_armature
    if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.pose:
        settings.status_message = "Receiving (Select Target Armature)"
        return 1.0 / 60.0

    settings.status_message = "Receiving Data"
    if latest_packet.get('key_sync_error'):
        settings.status_message = 'Preview ativo; falha nas chaves: ' + latest_packet['key_sync_error']
    from . import live_sync
    if (live_sync._source and latest_packet.get('bridge_source')
            and latest_packet['bridge_source'] != live_sync._source):
        settings.status_message = 'Cena Cascadeur mudou; reconecte para confirmar o rig'
        return 1.0/60.0
    if live_sync.poll_local(arm_obj, settings, context):
        settings.status_message = live_sync._last_error or 'Editando no Blender: insira chave ou Retomar preview'
        return 1.0/60.0
    if key_messages:
        try:
            old_revision = live_sync._revision
            live_sync.apply_keys(key_messages[-1], arm_obj, settings, context)
            if old_revision != live_sync._revision:
                _last_preview = None
        except Exception as error:
            settings.status_message = f'Falha sincronizando chaves: {error}'
            return 1.0/60.0
    if not latest_packet:
        return 1.0/60.0
    record = settings.record_animation
    signature = (arm_obj.as_pointer(), latest_packet['frame'], settings.pose_mapping_mode,
                 settings.translation_scale, settings.coord_conversion, latest_packet['bones'])
    if not record and signature == _last_preview:
        return 1.0 / max(10,settings.update_rate_fps)
    sync_frame = settings.sync_timeline_frame

    if record:
        # If recording keyframes, apply all drained frames in order
        for pkt in pose_packets:
            apply_packet_to_rig(pkt, arm_obj, settings, record=True, sync_frame=sync_frame, context=context)
    else:
        # Live preview: apply latest packet for lowest latency (2ms execution, no lag!)
        apply_packet_to_rig(latest_packet, arm_obj, settings, record=False, sync_frame=sync_frame, context=context)
    _last_preview = signature
    live_sync.mark_preview(arm_obj, context)

    # Force viewport redraw
    for window in context.window_manager.windows:
        if getattr(window, "screen", None):
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()

    return max(0.001, 1.0/settings.update_rate_fps - (time.perf_counter()-started))


class CASCADEUR_OT_live_link_start(bpy.types.Operator):
    bl_idname = "cascadeur.live_link_start"
    bl_label = "Start Live Link"
    bl_description = "Start listening for real-time animation packets from Cascadeur"

    def execute(self, context):
        global _last_preview
        _last_preview = None
        global _receiver_thread, _stop_event, _packet_queue, _timer_registered, _last_frame_applied, _initial_root_pos
        global _bone_match_cache, _armature_depth_cache, _pelvis_bone_cache
        _initial_root_pos = None
        _bone_match_cache.clear()
        _armature_depth_cache.clear()
        _pelvis_bone_cache.clear()
        _packet_arrival_times.clear()

        settings = context.scene.cascadeur_live_link

        arm = settings.target_armature
        if not arm:
            self.report({'ERROR'}, 'Escolha Target Armature');return {'CANCELLED'}
        if settings.pose_mapping_mode == 'GLOBAL_MATRIX' and any(b.use_connect for b in arm.data.bones):
            self.report({'ERROR'}, 'Pare e clique Permitir translacao dos joints em Coordinate & Bone Mapping');return {'CANCELLED'}
        pairs=[r for r in settings.bone_mapping if r.enabled]
        if (len({r.target for r in pairs})!=len(pairs) or len({r.source for r in pairs})!=len(pairs)
                or any(not r.source or not arm.pose.bones.get(r.target) for r in pairs)
                or (settings.mapping_only and not pairs)):
            self.report({'ERROR'}, 'Revise o perfil: pares vazios, duplicados ou ossos ausentes');return {'CANCELLED'}

        from . import live_sync
        live_sync.reset(settings.target_armature)
        settings.record_animation = False

        if not settings.target_armature:
            self.report({'WARNING'}, "Please select a Target Armature before starting Live Link.")

        # Stop any previous listener thread cleanly
        if _receiver_thread and _receiver_thread.is_alive():
            _stop_event.set()
            _receiver_thread.join(timeout=0.5)

        # Reset stop event and queue
        _stop_event.clear()
        _last_frame_applied = -1
        while not _packet_queue.empty():
            try:
                _packet_queue.get_nowait()
            except queue.Empty:
                break

        # Start listener thread
        _receiver_thread = threading.Thread(
            target=udp_listener_worker,
            args=(settings.host, settings.port, _packet_queue, _stop_event),
            daemon=True,
        )
        _receiver_thread.start()

        # Register bpy.app.timers callback for main-thread packet processing
        if not _timer_registered:
            bpy.app.timers.register(_tick_process_packets, first_interval=1.0 / 60.0, persistent=True)
            _timer_registered = True

        # Activate 3D Viewport HUD overlay
        from . import overlay
        overlay.register_overlay()

        settings.is_connected = True
        settings.packets_received = 0
        settings.status_message = f"Listening on {settings.host}:{settings.port}"
        send_cascadeur_command('live_start', {'stream_port': settings.port}, context=context)

        self.report({'INFO'}, f"Cascadeur Live Link started on {settings.host}:{settings.port}")
        return {'FINISHED'}


class CASCADEUR_OT_live_link_stop(bpy.types.Operator):
    bl_idname = "cascadeur.live_link_stop"
    bl_label = "Stop Live Link"
    bl_description = "Stop listening for Cascadeur animation packets"

    def execute(self, context):
        global _stop_event, _timer_registered
        _stop_event.set()

        # Deactivate 3D Viewport HUD overlay
        from . import overlay
        overlay.unregister_overlay()

        if _timer_registered:
            try:
                bpy.app.timers.unregister(_tick_process_packets)
            except Exception:
                pass
            _timer_registered = False

        settings = context.scene.cascadeur_live_link
        settings.is_connected = False
        settings.stream_fps = 0.0
        settings.viewport_fps = 0.0
        _packet_arrival_times.clear()
        settings.status_message = "Disconnected"
        self.report({'INFO'}, "Stopping Cascadeur Live Link...")
        return {'FINISHED'}


class CASCADEUR_OT_reset_pose(bpy.types.Operator):
    bl_idname = "cascadeur.reset_pose"
    bl_label = "Reset Pose"
    bl_description = "Reset all pose bones on the target armature to default rest transforms"

    @classmethod
    def poll(cls, context):
        settings = context.scene.cascadeur_live_link
        return settings.target_armature is not None

    def execute(self, context):
        global _initial_root_pos, _pelvis_bone_cache
        _initial_root_pos = None
        _pelvis_bone_cache.clear()

        settings = context.scene.cascadeur_live_link
        arm_obj = settings.target_armature
        if not arm_obj or not arm_obj.pose:
            self.report({'WARNING'}, "Target armature has no pose data.")
            return {'CANCELLED'}

        for pbone in arm_obj.pose.bones:
            pbone.location = Vector((0.0, 0.0, 0.0))
            pbone.rotation_quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
            pbone.rotation_euler = (0.0, 0.0, 0.0)
            pbone.scale = Vector((1.0, 1.0, 1.0))

        if getattr(context, "area", None):
            context.area.tag_redraw()
        self.report({'INFO'}, f"Reset pose transforms for {len(arm_obj.pose.bones)} bones.")
        return {'FINISHED'}


class CASCADEUR_OT_clear_keyframes(bpy.types.Operator):
    bl_idname = "cascadeur.clear_keyframes"
    bl_label = "Clear Keyframes"
    bl_description = "Remove all animation keyframes from the target armature"

    @classmethod
    def poll(cls, context):
        settings = context.scene.cascadeur_live_link
        return settings.target_armature is not None

    def execute(self, context):
        settings = context.scene.cascadeur_live_link
        arm_obj = settings.target_armature
        if not arm_obj:
            self.report({'WARNING'}, "No target armature set.")
            return {'CANCELLED'}

        if arm_obj.animation_data and arm_obj.animation_data.action:
            bpy.data.actions.remove(arm_obj.animation_data.action)
            self.report({'INFO'}, "Cleared all animation keyframes.")
        else:
            self.report({'INFO'}, "No keyframes to clear.")
        return {'FINISHED'}


class CASCADEUR_OT_detect_scale(bpy.types.Operator):
    """Calculate translation scale between Cascadeur and Blender characters"""
    bl_idname = "cascadeur.detect_scale"
    bl_label = "Auto-Calculate Scale"
    bl_description = "Calculates the scale ratio by comparing Cascadeur and Blender character proportions"

    @classmethod
    def poll(cls, context):
        settings = context.scene.cascadeur_live_link
        return settings.target_armature is not None

    def execute(self, context):
        settings = context.scene.cascadeur_live_link
        arm_obj = settings.target_armature
        if not arm_obj:
            self.report({'WARNING'}, "Please select a Target Armature first.")
            return {'CANCELLED'}

        global _last_packet_received
        if _last_packet_received:
            detected = calculate_translation_scale(_last_packet_received, arm_obj, settings)
            if detected:
                settings.translation_scale = detected
                self.report({'INFO'}, f"Calculated translation scale: {detected:.4f}")
                return {'FINISHED'}

        self.report({'INFO'}, "Waiting for live packet from Cascadeur to calculate scale...")
        settings.auto_calc_scale = True
        return {'FINISHED'}


class CASCADEUR_OT_set_scale_preset(bpy.types.Operator):
    """Set standard translation scale preset"""
    bl_idname = "cascadeur.set_scale_preset"
    bl_label = "Set Scale Preset"
    bl_description = "Set standard translation scale multiplier"

    preset_value: bpy.props.FloatProperty(name="Scale Preset", default=0.01)

    def execute(self, context):
        context.scene.cascadeur_live_link.translation_scale = self.preset_value
        context.scene.cascadeur_live_link.auto_calc_scale = False
        self.report({'INFO'}, f"Set translation scale to {self.preset_value}")
        return {'FINISHED'}


class CASCADEUR_OT_calibrate_rest_pose(bpy.types.Operator):
    """Calibrate Rest Pose / T-Pose using current pose from Cascadeur stream"""
    bl_idname = "cascadeur.calibrate_rest_pose"
    bl_label = "Calibrate Rest Pose"
    bl_description = "Capture the current pose from Cascadeur as the Rest/T-Pose to calibrate bone offsets"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _last_packet_received
        settings = context.scene.cascadeur_live_link
        arm_obj = settings.target_armature
        if not arm_obj:
            self.report({'WARNING'}, "Please select a Target Armature first.")
            return {'CANCELLED'}

        if not _last_packet_received:
            self.report({'WARNING'}, "No packet received from Cascadeur yet. Please start Live Link first.")
            return {'CANCELLED'}

        count = calibrate_rest_pose(_last_packet_received, arm_obj, settings)
        if count > 0:
            self.report({'INFO'}, f"✓ Calibrated rest pose for {count} bones.")
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "Could not calibrate any bones from last packet.")
            return {'CANCELLED'}


class CASCADEUR_OT_clear_calibration(bpy.types.Operator):
    """Clear Rest Pose Calibration data"""
    bl_idname = "cascadeur.clear_calibration"
    bl_label = "Clear Calibration"
    bl_description = "Remove cached rest pose offsets and return to standard mapping"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global _rest_pose_cache, _rest_pose_local_cache
        _rest_pose_cache.clear()
        _rest_pose_local_cache.clear()
        settings = context.scene.cascadeur_live_link
        settings.is_rest_pose_calibrated = False
        settings.calibrated_bone_count = 0
        settings.rest_pose_data = ""
        self.report({'INFO'}, "Rest pose calibration cleared.")
        return {'FINISHED'}


def extract_armature_pose(arm_obj, settings):
    """
    Extracts pose bone transforms from arm_obj and converts them to Cascadeur space.
    Calculates calibration-free 3D delta rotations and global positions:
      - delta_rot: 3D rotation applied in Blender relative to the bone's native rest matrix:
                   R_delta = pbone.matrix.to_3x3() @ pbone.bone.matrix_local.to_3x3().inverted()
                   In rest pose, delta_rot is Identity (0 deg).
                   When posed, delta_rot represents the true 3D rotation in Armature world space.
      - g_pos: 3D position of pbone.head in Cascadeur Y-up cm coordinate space.
      - pos: Local location of pbone (for root, g_pos; for children, relative offset).
      - rot: Fallback local rotation delta relative to parent bone.
    Returns a dictionary mapping bone name to pos, rot, g_pos, g_rot, delta_rot, is_root.
    """
    scale_setting = getattr(settings, "translation_scale", 1.0) or 1.0

    pose_bones = arm_obj.pose.bones

    # Determine root/pelvis bone
    root_pb = next((pb for pb in pose_bones if is_pelvis_or_root(pb)), None)
    if root_pb is None:
        root_pb = get_pelvis_bone(pose_bones)

    is_cc5 = is_rig_cc5(arm_obj, settings)

    # Detect whether Armature rest coordinates have Y as UP (like Cascy: abs(head_local.y) > abs(head_local.z))
    # or Z as UP (like CC5 or standard Blender: abs(head_local.z) >= abs(head_local.y))
    if root_pb is not None:
        ref_h = root_pb.bone.head_local
        is_y_up = (abs(ref_h.y) > abs(ref_h.z)) and not is_cc5
        pelvis_h = abs(root_pb.head.y) if is_y_up else abs(root_pb.head.z)
    else:
        is_y_up = not is_cc5
        pelvis_h = 100.0

    if pelvis_h < 5.0 and scale_setting < 0.5:
        pos_scale = 1.0 / max(scale_setting, 1e-5)
    else:
        pos_scale = 1.0

    bones_data = {}

    for pbone in pose_bones:
        name = pbone.name
        # Never send helper, twist, share, facial, or subsidiary bones to Cascadeur
        if is_cc5 and is_helper_bone(name):
            continue
        is_root = is_pelvis_or_root(pbone)

        # 1. 3D Global Position in Cascadeur space (Y-up cm)
        if is_y_up:
            # Cascy / Y-up rig: Armature space matches Cascadeur space directly (X=right, Y=up, Z=forward)
            c_x = pbone.head.x
            c_y = pbone.head.y
            c_z = pbone.head.z
        else:
            # CC5 / Blender Z-up rig: (X=right, Y=forward, Z=up) -> Cascadeur (X=right, Y=up, Z=forward)
            c_x = pbone.head.x
            c_y = pbone.head.z
            c_z = -pbone.head.y

        if pos_scale != 1.0:
            c_x *= pos_scale
            c_y *= pos_scale
            c_z *= pos_scale

        g_pos = [
            round(float(c_x), 5),
            round(float(c_y), 5),
            round(float(c_z), 5),
        ]

        # 2. 3D Delta Rotation in Armature space (Y-up, right-handed)
        # R_delta = pbone.matrix.to_3x3() @ pbone.bone.matrix_local.to_3x3().inverted()
        # For non-Y-up rigs (CC5/standard Blender Z-up), transform rotation to Cascadeur Y-up space via change of basis:
        # R_c = M_conv @ R_b @ M_conv.T
        r_delta_3x3 = pbone.matrix.to_3x3() @ pbone.bone.matrix_local.to_3x3().inverted()
        if not is_y_up:
            m_conv = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)))
            r_delta_3x3 = m_conv @ r_delta_3x3 @ m_conv.transposed()

        q_delta = r_delta_3x3.to_quaternion().normalized()
        delta_rot = [
            round(float(q_delta.w), 7),
            round(float(q_delta.x), 7),
            round(float(q_delta.y), 7),
            round(float(q_delta.z), 7),
        ]

        # 3. Position for transmission:
        # ONLY the primary pelvis/root bone is permitted to translate in world space.
        # All child bones strictly rotate and never translate (pos = 0) to avoid mesh dislocation.
        if is_root:
            pos = list(g_pos)
        else:
            pos = [0.0, 0.0, 0.0]

        # 4. Relative local rotation and unposed detection:
        parent_pb = pbone.parent
        while parent_pb and is_helper_bone(parent_pb.name):
            parent_pb = parent_pb.parent

        if parent_pb:
            parent_r_delta = parent_pb.matrix.to_3x3() @ parent_pb.bone.matrix_local.to_3x3().inverted()
            if not is_y_up:
                parent_r_delta = m_conv @ parent_r_delta @ m_conv.transposed()
            local_delta = parent_r_delta.inverted() @ r_delta_3x3
            q_cl = local_delta.to_quaternion().normalized()
            is_unposed = abs(q_cl.angle) < 1e-4
        else:
            q_cl = q_delta
            is_unposed = abs(q_cl.angle) < 1e-4

        rot = [
            round(float(q_cl.w), 7),
            round(float(q_cl.x), 7),
            round(float(q_cl.y), 7),
            round(float(q_cl.z), 7),
        ]

        bones_data[name] = {
            "pos": pos,
            "rot": rot,
            "g_pos": g_pos,
            "g_rot": delta_rot,
            "delta_rot": delta_rot,
            "is_root": is_root,
            "is_unposed": is_unposed,
        }

    return bones_data


class CASCADEUR_OT_send_pose_to_cascadeur(bpy.types.Operator):
    """Send current active pose from Blender armature to Cascadeur via UDP"""
    bl_idname = "cascadeur.send_pose_to_cascadeur"
    bl_label = "Send Pose to Cascadeur"
    bl_description = "Capture the current pose of the target armature and transmit it to Cascadeur via UDP"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        settings = context.scene.cascadeur_live_link
        if settings.target_armature:
            return True
        return context.active_object and context.active_object.type == 'ARMATURE'

    def execute(self, context):
        settings = context.scene.cascadeur_live_link
        arm_obj = settings.target_armature or context.active_object
        if not arm_obj or arm_obj.type != 'ARMATURE' or not arm_obj.pose:
            self.report({'WARNING'}, "Please select a Target Armature with pose bones first.")
            return {'CANCELLED'}

        if not settings.target_armature:
            settings.target_armature = arm_obj

        if settings.pose_mapping_mode in {'GLOBAL_DELTA', 'GLOBAL_MATRIX'}:
            try:
                from .keyframe_bridge import send_pose
                reply = send_pose(arm_obj, settings, context)
                from .live_sync import acknowledge_local
                acknowledge_local(arm_obj, context)
                self.report({'INFO'}, reply.get('message', 'Pose confirmada no Cascadeur'))
                return {'FINISHED'}
            except Exception as error:
                self.report({'ERROR'}, str(error))
                return {'CANCELLED'}

        bones_data = extract_armature_pose(arm_obj, settings)
        if not bones_data:
            self.report({'WARNING'}, "No pose bones found on target armature.")
            return {'CANCELLED'}

        target_mode = getattr(settings, "send_pose_target_frame", "CURRENT")
        if target_mode == "CURRENT":
            frame_val = None
            frame_desc = "active frame"
        else:
            frame_val = int(context.scene.frame_current)
            frame_desc = f"frame {frame_val}"

        host = settings.host
        port = getattr(settings, "cascadeur_receive_port", 9998)

        payload = {
            "version": "1.0",
            "type": "set_pose",
            "frame": frame_val,
            "fps": int(context.scene.render.fps),
            "bones": bones_data,
        }

        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            sock = get_send_socket()
            sock.sendto(data_bytes, (host, port))
            if settings.is_connected:
                self.report({'WARNING'}, f"Sent pose to Cascadeur {frame_desc}, but Live Link is currently receiving. Stop Live Link if you want to keep posing in Blender.")
            else:
                self.report({'INFO'}, f"✓ Sent pose ({len(bones_data)} bones) to Cascadeur {frame_desc} on {host}:{port}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Failed to send pose to Cascadeur: {e}")
            return {'CANCELLED'}


def send_cascadeur_command(cmd, payload=None, host=None, port=None, context=None):
    """Send a JSON command packet to Cascadeur via UDP."""
    target_host = host
    target_port = port
    if context and hasattr(context, "scene") and hasattr(context.scene, "cascadeur_live_link"):
        settings = context.scene.cascadeur_live_link
        if not target_host:
            target_host = settings.host
        if not target_port:
            target_port = settings.cascadeur_receive_port

    if not target_host:
        target_host = "127.0.0.1"
    if not target_port:
        target_port = 9998

    sock = get_send_socket()
    msg = {"cmd": cmd}
    if payload:
        msg.update(payload)
    try:
        data = json.dumps(msg).encode("utf-8")
        sock.sendto(data, (target_host, target_port))
        return True, f"Sent '{cmd}' to Cascadeur ({target_host}:{target_port})"
    except Exception as e:
        return False, f"Failed to send '{cmd}': {e}"


class CASCADEUR_OT_timeline_to_first(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_to_first"
    bl_label = "First Frame"
    bl_description = "Jump Cascadeur timeline to the first frame (frame 0)"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_to_first", context=context)
        if ok:
            self.report({'INFO'}, "Cascadeur: Jumped to first frame")
        else:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_play_pause(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_play_pause"
    bl_label = "Play / Pause"
    bl_description = "Toggle animation playback in Cascadeur"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_play_pause", context=context)
        if ok:
            self.report({'INFO'}, "Cascadeur: Play/Pause toggled")
        else:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_prev_frame(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_prev_frame"
    bl_label = "Previous Frame"
    bl_description = "Step backward one frame in Cascadeur"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_prev_frame", context=context)
        if not ok:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_next_frame(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_next_frame"
    bl_label = "Next Frame"
    bl_description = "Step forward one frame in Cascadeur"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_next_frame", context=context)
        if not ok:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_prev_key(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_prev_key"
    bl_label = "Previous Key"
    bl_description = "Jump to previous keyframe in Cascadeur"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_prev_key", context=context)
        if not ok:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_next_key(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_next_key"
    bl_label = "Next Key"
    bl_description = "Jump to next keyframe in Cascadeur"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_next_key", context=context)
        if not ok:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_to_last(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_to_last"
    bl_label = "Last Frame"
    bl_description = "Jump Cascadeur timeline to the last frame"

    def execute(self, context):
        ok, msg = send_cascadeur_command("timeline_to_last", context=context)
        if ok:
            self.report({'INFO'}, "Cascadeur: Jumped to last frame")
        else:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


class CASCADEUR_OT_timeline_sync_active_frame(bpy.types.Operator):
    bl_idname = "cascadeur.timeline_sync_active_frame"
    bl_label = "Sync Frame to Cascadeur"
    bl_description = "Jump Cascadeur playhead to Blender's current active frame"

    def execute(self, context):
        active_frame = int(context.scene.frame_current)
        ok, msg = send_cascadeur_command("timeline_set_frame", payload={"frame": active_frame}, context=context)
        if ok:
            self.report({'INFO'}, f"Cascadeur: Set to frame {active_frame}")
        else:
            self.report({'WARNING'}, msg)
        return {'FINISHED'}


classes = (
    CASCADEUR_OT_live_link_start,
    CASCADEUR_OT_live_link_stop,
    CASCADEUR_OT_reset_pose,
    CASCADEUR_OT_clear_keyframes,
    CASCADEUR_OT_detect_scale,
    CASCADEUR_OT_set_scale_preset,
    CASCADEUR_OT_calibrate_rest_pose,
    CASCADEUR_OT_clear_calibration,
    CASCADEUR_OT_send_pose_to_cascadeur,
    CASCADEUR_OT_timeline_to_first,
    CASCADEUR_OT_timeline_play_pause,
    CASCADEUR_OT_timeline_prev_frame,
    CASCADEUR_OT_timeline_next_frame,
    CASCADEUR_OT_timeline_prev_key,
    CASCADEUR_OT_timeline_next_key,
    CASCADEUR_OT_timeline_to_last,
    CASCADEUR_OT_timeline_sync_active_frame,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    global _stop_event, _timer_registered, _send_socket
    _stop_event.set()
    if _timer_registered:
        try:
            bpy.app.timers.unregister(_tick_process_packets)
        except Exception:
            pass
        _timer_registered = False
    if _send_socket is not None:
        try:
            _send_socket.close()
        except Exception:
            pass
        _send_socket = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
