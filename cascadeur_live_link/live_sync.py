"""Live preview plus sparse Action keys; Blender changes are sent only after key edits."""
import time
import bpy
from . import keyframe_bridge as bridge

_baseline = None
_revision = None
_source = None
_preview = None
_preview_frame = None
_editing = False
_last_poll = 0
_key_cache = {}
_failed_state = None
_last_error = None


def curves(arm):
    ad = arm.animation_data
    if not ad or not ad.action:
        return []
    slot = ad.action_slot
    return [fc for layer in ad.action.layers for strip in layer.strips for bag in strip.channelbags
            if slot is None or bag.slot_handle == slot.handle for fc in bag.fcurves]


def fingerprints(arm):
    result = {}
    for fc in curves(arm):
        if not fc.data_path.startswith('pose.bones['):
            continue
        for kp in fc.keyframe_points:
            frame = float(kp.co.x)
            result.setdefault(frame, []).append((fc.data_path, fc.array_index, round(float(kp.co.y), 7)))
    return {f:tuple(sorted(values)) for f,values in result.items()}


def pose_signature(arm):
    return tuple(round(v, 6) for pb in arm.pose.bones for row in pb.matrix_basis for v in row)


def reset(arm=None):
    global _baseline, _revision, _source, _preview, _preview_frame, _editing, _last_poll, _key_cache, _failed_state, _last_error
    _baseline = fingerprints(arm) if arm else None
    _revision = _source = _preview = _preview_frame = None
    _editing = False
    _last_poll = 0
    _key_cache = {}
    _failed_state = _last_error = None


def edited_frames(previous, current):
    """Only new keys/value edits; removing channels alone is not a pose edit."""
    edited = []
    for frame, signature in current.items():
        before = {(path,index):value for path,index,value in previous.get(frame, ())}
        if any(before.get((path,index)) != value for path,index,value in signature):
            edited.append(frame)
    return edited


def mark_preview(arm, context):
    global _preview, _preview_frame
    _preview = pose_signature(arm)
    _preview_frame = context.scene.frame_current


def acknowledge_local(arm, context):
    global _baseline, _editing, _failed_state, _last_error
    _baseline = fingerprints(arm)
    _editing = False
    _failed_state = _last_error = None
    mark_preview(arm, context)


def send_frames(arm, settings, context, frames):
    original = context.scene.frame_current
    packets = []
    # Capture all frames first, while the timer cannot overwrite the local pose.
    try:
        for frame in sorted(frames):
            if int(frame) != frame:
                raise ValueError('Cascadeur usa frames inteiros; chave fracionaria encontrada')
            context.scene.frame_set(int(frame))
            context.view_layer.update()
            packets.append((int(frame), bridge.capture_pose(arm, settings)))
    finally:
        context.scene.frame_set(original)
    for frame, bones in packets:
        bridge.send_bones(bones, settings, frame)
    return len(packets)


def poll_local(arm, settings, context):
    global _baseline, _last_poll, _editing, _failed_state, _last_error
    if not settings.sync_blender_keys:
        return _editing
    current_pose = pose_signature(arm)
    playing = bool(context.screen and context.screen.is_animation_playing)
    if (_preview is not None and context.scene.frame_current == _preview_frame
            and current_pose != _preview and not playing):
        _editing = True
    if time.monotonic() - _last_poll < 0.2:
        return _editing
    _last_poll = time.monotonic()
    current = fingerprints(arm)
    if _failed_state == current:
        return True
    if _baseline is None:
        _baseline = current
        return _editing
    changed = edited_frames(_baseline, current)
    if changed:
        try:
            count = send_frames(arm, settings, context, changed)
            settings.status_message = f'Blender → Cascadeur: {count} keyframes confirmados'
            _baseline = fingerprints(arm)
            _editing = False
            _failed_state = _last_error = None
            mark_preview(arm, context)
            return True  # Discard queued snapshots sampled before the acknowledged edit.
        except Exception as error:
            settings.status_message = f'Falha ao enviar chaves: {error}'
            _last_error = settings.status_message
            _failed_state = current
            _editing = True
    else:
        _baseline = current
    return _editing


def apply_keys(message, arm, settings, context):
    global _baseline, _revision, _source, _key_cache
    if not settings.sync_cascadeur_keys or message.get('revision') == _revision:
        return
    if _source is not None and message['source'] != _source:
        settings.status_message = 'Cena Cascadeur mudou; reconecte para confirmar o rig'
        return
    original_frame = context.scene.frame_current
    arm.animation_data_create()
    # Own a separate Action so original work can always be recovered.
    if _source is None:
        if arm.animation_data.action:
            arm.animation_data.action = arm.animation_data.action.copy()
            arm.animation_data.action.name = 'Cascadeur Sync'
        else:
            arm.animation_data.action = bpy.data.actions.new('Cascadeur Sync')
    names = set()
    from . import operators
    for name in message['bone_frames']:
        pb = operators.find_matching_bone(arm.pose.bones, name, settings.bone_name_prefix.strip(),
             smart_map=settings.auto_map_synonyms, is_cc5=operators.is_rig_cc5(arm, settings))
        if pb:
            names.add(pb.name)
    paths = {arm.pose.bones[n].path_from_id() + '.' + channel
             for n in names for channel in ('location', 'rotation_quaternion', 'rotation_euler', 'rotation_axis_angle')}
    allowed = {}
    for source_name, frames in message['bone_frames'].items():
        pb = operators.find_matching_bone(arm.pose.bones, source_name, settings.bone_name_prefix.strip(),
             smart_map=settings.auto_map_synonyms, is_cc5=operators.is_rig_cc5(arm, settings))
        if pb:
            for channel in ('location','rotation_quaternion','rotation_euler','rotation_axis_angle'):
                allowed[pb.path_from_id()+'.'+channel] = set(frames)
    for fc in curves(arm):
        if fc.data_path in paths:
            if _source is None:
                fc.keyframe_points.clear()
            else:
                for i in range(len(fc.keyframe_points)-1,-1,-1):
                    kp = fc.keyframe_points[i]
                    if kp.co.x not in allowed.get(fc.data_path,set()):
                        fc.keyframe_points.remove(kp, fast=True)
                fc.update()
    try:
        for packet in message['entries']:
            if packet['bones'] and _key_cache.get(packet['frame']) != packet['bones']:
                bridge.commit_pose(packet, arm, settings, context)
    finally:
        context.scene.frame_set(original_frame)
    _baseline = fingerprints(arm)
    _revision, _source = message['revision'], message['source']
    _key_cache = {p['frame']:p['bones'] for p in message['entries']}
    mark_preview(arm, context)
    settings.status_message = f'Chaves sincronizadas: {len(message["entries"])} frames'


class CASCADEUR_OT_send_key_range(bpy.types.Operator):
    bl_idname = 'cascadeur.send_key_range'
    bl_label = 'Enviar keyframes do intervalo'
    bl_description = 'Envia as poses nos frames com chaves da Action ativa dentro do intervalo'

    def execute(self, context):
        global _baseline, _editing
        settings = context.scene.cascadeur_live_link
        arm = settings.target_armature
        if arm is None:
            self.report({'ERROR'}, 'Escolha Target Character Rig')
            return {'CANCELLED'}
        frames = [f for f in fingerprints(arm) if settings.key_range_start <= f <= settings.key_range_end]
        if not frames:
            self.report({'ERROR'}, 'Nenhuma chave da Action neste intervalo')
            return {'CANCELLED'}
        try:
            count = send_frames(arm, settings, context, frames)
            acknowledge_local(arm, context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        self.report({'INFO'}, f'{count} keyframes confirmados no Cascadeur')
        return {'FINISHED'}


class CASCADEUR_OT_resume_preview(bpy.types.Operator):
    bl_idname = 'cascadeur.resume_preview'
    bl_label = 'Retomar preview do Cascadeur'

    def execute(self, context):
        global _editing, _failed_state, _last_error
        _editing = False
        _failed_state = _last_error = None
        arm = context.scene.cascadeur_live_link.target_armature
        if arm:
            mark_preview(arm, context)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(CASCADEUR_OT_send_key_range)
    bpy.utils.register_class(CASCADEUR_OT_resume_preview)


def unregister():
    bpy.utils.unregister_class(CASCADEUR_OT_resume_preview)
    bpy.utils.unregister_class(CASCADEUR_OT_send_key_range)
