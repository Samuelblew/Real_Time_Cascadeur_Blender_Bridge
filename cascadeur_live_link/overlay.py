"""
3D Viewport HUD Overlay Indicator for Cascadeur Live Link.
Renders an on-screen status badge in the 3D Viewport when Live Link is active.
"""

import collections
import time
import math
import bpy
import blf

_overlay_handler = None
_shader = None
_draw_times = collections.deque(maxlen=120)


def _get_2d_shader():
    """Returns the 2D uniform color shader compatible with Blender 3.x and 4.x+."""
    global _shader
    if _shader is not None:
        return _shader
    try:
        import gpu
        # Blender 4.0+ uses 'UNIFORM_COLOR', older versions used '2D_UNIFORM_COLOR'
        try:
            _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except Exception:
            _shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
    except Exception:
        _shader = None
    return _shader


def _draw_rect_2d(shader, x1, y1, x2, y2, color):
    """Draws a solid 2D rectangle using two triangles."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    vertices = [
        (x1, y1), (x2, y1), (x2, y2),
        (x1, y1), (x2, y2), (x1, y2)
    ]
    batch = batch_for_shader(shader, 'TRIS', {"pos": vertices})
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw_circle_2d(shader, cx, cy, radius, color, segments=16):
    """Draws a solid 2D circle using triangle fan composed of triangles."""
    from gpu_extras.batch import batch_for_shader

    vertices = []
    angle_step = (2.0 * math.pi) / segments
    for i in range(segments):
        a1 = i * angle_step
        a2 = (i + 1) * angle_step
        p1 = (cx + radius * math.cos(a1), cy + radius * math.sin(a1))
        p2 = (cx + radius * math.cos(a2), cy + radius * math.sin(a2))
        vertices.extend([(cx, cy), p1, p2])

    batch = batch_for_shader(shader, 'TRIS', {"pos": vertices})
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_viewport_overlay():
    """Main draw callback executed on SpaceView3D 'POST_PIXEL'."""
    try:
        context = bpy.context
        if not context or not hasattr(context, "scene") or not context.scene:
            return

        settings = getattr(context.scene, "cascadeur_live_link", None)
        if not settings or not settings.is_connected:
            return

        if not getattr(settings, "show_viewport_overlay", True):
            return

        region = getattr(context, "region", None)
        if not region:
            return

        shader = _get_2d_shader()
        if not shader:
            return

        import gpu

        # 0. Measure Viewport FPS via sliding window over 1.0s
        now = time.time()
        _draw_times.append(now)
        while _draw_times and (now - _draw_times[0]) > 1.0:
            _draw_times.popleft()

        if len(_draw_times) >= 2:
            dt = _draw_times[-1] - _draw_times[0]
            view_fps = ((len(_draw_times) - 1) / dt) if dt > 0.001 else 0.0
        else:
            view_fps = 0.0

        try:
            settings.viewport_fps = view_fps
        except Exception:
            pass

        recv_fps = getattr(settings, "stream_fps", 0.0)

        # 1. Text Metrics & Dynamic Badge Dimensions
        font_id = 0

        title_text = "BLENDER LIVE LINK"
        try:
            blf.size(font_id, 10)
        except TypeError:
            blf.size(font_id, 10, 72)
        w_title = blf.dimensions(font_id, title_text)[0]

        fps_recv_str = f"Recv: {recv_fps:.1f} FPS"
        fps_sep_str = "  |  "
        fps_view_str = f"View: {view_fps:.1f} FPS"
        try:
            blf.size(font_id, 9)
        except TypeError:
            blf.size(font_id, 9, 72)
        w_recv = blf.dimensions(font_id, fps_recv_str)[0]
        w_sep = blf.dimensions(font_id, fps_sep_str)[0]
        w_view = blf.dimensions(font_id, fps_view_str)[0]
        fps_total_w = w_recv + w_sep + w_view

        frame_num = getattr(settings, "last_frame_received", 0)
        target_name = settings.target_armature.name if settings.target_armature else "No Rig"
        subtitle_text = f"Frame: {frame_num}  •  {target_name}"
        try:
            blf.size(font_id, 9)
        except TypeError:
            blf.size(font_id, 9, 72)
        w_sub = blf.dimensions(font_id, subtitle_text)[0]

        # Badge Dimensions and Position (Bottom-left corner)
        badge_w = max(340.0, 32.0 + w_title + 24.0 + fps_total_w + 16.0, 32.0 + w_sub + 20.0)
        badge_h = 44.0
        pad_x = 24.0
        pad_y = 28.0
        x = pad_x
        y = pad_y

        # Enable alpha blending
        gpu.state.blend_set('ALPHA')

        try:
            # 2. Dark translucent pill background
            bg_color = (0.07, 0.08, 0.11, 0.88)
            _draw_rect_2d(shader, x, y, x + badge_w, y + badge_h, bg_color)

            # 3. Subtle accent border line at bottom of pill
            border_color = (0.18, 0.82, 0.44, 0.5)
            _draw_rect_2d(shader, x, y, x + badge_w, y + 2.0, border_color)

            # 4. Pulsing indicator dot
            pulse = 0.85 + 0.15 * math.sin(time.time() * 4.0)
            dot_cx = x + 16.0
            dot_cy = y + badge_h * 0.5
            dot_radius = 5.0

            # Outer glow
            glow_color = (0.18, 0.85, 0.44, 0.3 * pulse)
            _draw_circle_2d(shader, dot_cx, dot_cy, dot_radius + 3.0, glow_color, segments=16)

            # Core dot
            core_color = (0.2, 0.95, 0.5, 0.95)
            _draw_circle_2d(shader, dot_cx, dot_cy, dot_radius, core_color, segments=16)

            # 5. Render Text
            # Line 1: Title
            try:
                blf.size(font_id, 10)
            except TypeError:
                blf.size(font_id, 10, 72)
            blf.color(font_id, 1.0, 1.0, 1.0, 0.95)
            blf.position(font_id, x + 32.0, y + 25.0, 0.0)
            blf.draw(font_id, title_text)

            # Line 1: Right-aligned FPS Indicators
            fps_start_x = x + badge_w - 14.0 - fps_total_w
            try:
                blf.size(font_id, 9)
            except TypeError:
                blf.size(font_id, 9, 72)

            # Recv FPS (vibrant emerald green)
            blf.color(font_id, 0.35, 0.95, 0.55, 0.95)
            blf.position(font_id, fps_start_x, y + 25.0, 0.0)
            blf.draw(font_id, fps_recv_str)

            # Separator (muted divider)
            blf.color(font_id, 0.45, 0.5, 0.6, 0.7)
            blf.position(font_id, fps_start_x + w_recv, y + 25.0, 0.0)
            blf.draw(font_id, fps_sep_str)

            # Viewport FPS (vibrant cyan)
            blf.color(font_id, 0.35, 0.85, 1.0, 0.95)
            blf.position(font_id, fps_start_x + w_recv + w_sep, y + 25.0, 0.0)
            blf.draw(font_id, fps_view_str)

            # Line 2: Subtitle (Frame & Armature info)
            blf.color(font_id, 0.65, 0.75, 0.85, 0.85)
            blf.position(font_id, x + 32.0, y + 9.0, 0.0)
            blf.draw(font_id, subtitle_text)

        finally:
            gpu.state.blend_set('NONE')

    except Exception:
        pass


def _force_viewport_redraw():
    """Force all 3D Viewport areas to redraw immediately."""
    try:
        context = bpy.context
        if context and hasattr(context, "window_manager") and context.window_manager:
            for window in context.window_manager.windows:
                if getattr(window, "screen", None):
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
    except Exception:
        pass


def register_overlay():
    """Register the draw handler with SpaceView3D."""
    global _overlay_handler
    _draw_times.clear()
    if _overlay_handler is None:
        try:
            _overlay_handler = bpy.types.SpaceView3D.draw_handler_add(
                draw_viewport_overlay,
                (),
                'WINDOW',
                'POST_PIXEL'
            )
        except Exception as e:
            print(f"[Cascadeur Live Link] Could not register viewport overlay: {e}")
    _force_viewport_redraw()


def unregister_overlay():
    """Unregister the draw handler from SpaceView3D and force immediate redraw."""
    global _overlay_handler
    _draw_times.clear()
    if _overlay_handler is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_overlay_handler, 'WINDOW')
        except Exception:
            pass
        _overlay_handler = None
    _force_viewport_redraw()
