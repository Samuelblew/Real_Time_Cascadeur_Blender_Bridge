"""Explicit, frame-checked transfers; no continuous stream required."""
import json
import socket
import time
import uuid

import bpy
from mathutils import Matrix, Quaternion, Vector
from . import operators


def capture_pose(arm, settings):
    conversion = (Matrix.Rotation(-1.5707963267948966, 4, 'X')
                  if settings.coord_conversion == 'Y_UP_TO_Z_UP' else Matrix.Identity(4))
    cq = conversion.to_quaternion()
    bones = {}
    for pb in arm.pose.bones:
        world = arm.matrix_world @ pb.matrix
        rest = arm.matrix_world @ pb.bone.matrix_local
        delta = cq @ (world.to_quaternion() @ rest.to_quaternion().inverted()) @ cq.inverted()
        pos = conversion @ world.translation / settings.translation_scale
        bones[pb.name] = {'delta_rot': list(delta.normalized()), 'g_rot': list(delta.normalized()),
                         'g_pos': list(pos), 'pos': list(pos), 'is_root': operators.is_pelvis_or_root(pb),
                         'is_unposed': False}
    return bones


def send_pose(arm, settings, context, timeout=4.0):
    bones = capture_pose(arm, settings)
    return send_bones(bones, settings, context.scene.frame_current, timeout)


def send_bones(bones, settings, frame, timeout=4.0):
    request_id = uuid.uuid4().hex
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1', 0))
        sock.settimeout(timeout)
        payload = {'cmd': 'keyframe_apply_pose', 'request_id': request_id,
                   'reply_port': sock.getsockname()[1], 'frame': frame, 'bones': bones}
        encoded = json.dumps(payload).encode()
        if len(encoded) > 65000:
            raise ValueError('Rig excede o tamanho do pacote de envio; selecione um rig de deformacao menor')
        sock.sendto(encoded, (settings.host, settings.cascadeur_receive_port))
        data, _ = sock.recvfrom(65535)
        reply = json.loads(data)
        if reply.get('request_id') != request_id or reply.get('frame') != payload['frame']:
            raise ValueError('Confirmacao de frame invalida')
        if not reply.get('ok'):
            raise ValueError(reply.get('error', 'Cascadeur rejeitou a pose'))
        return reply


def request_pose(host, port, frame, timeout=3.0):
    request_id = uuid.uuid4().hex
    chunks = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.settimeout(timeout)
        payload = {"cmd": "keyframe_snapshot", "request_id": request_id,
                   "frame": int(frame), "reply_port": sock.getsockname()[1]}
        sock.sendto(json.dumps(payload).encode(), (host, port))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            data, _ = sock.recvfrom(65535)
            packet = json.loads(data)
            if packet.get("request_id") != request_id:
                continue
            if packet.get("error"):
                raise ValueError(packet["error"])
            if packet.get("frame") != frame:
                raise ValueError("Cascadeur respondeu com outro frame; nenhuma chave gravada")
            count, index = packet.get("parts", 1), packet.get("part", 0)
            if not isinstance(count, int) or not 1 <= count <= 1024 or not 0 <= index < count:
                raise ValueError("Resposta fragmentada invalida")
            chunks[index] = packet
            if len(chunks) == count:
                result = dict(packet)
                result["bones"] = {}
                for i in range(count):
                    result["bones"].update(chunks[i]["bones"])
                return result
    raise TimeoutError("Cascadeur nao respondeu. Execute Start Keyframe Bridge nele.")


def commit_pose(packet, arm, settings, context, record=True, sync_frame=True):
    if not packet.get("bones"):
        raise ValueError("Cascadeur nao retornou joints")
    matched = [operators.find_matching_bone(arm.pose.bones, name,
               settings.bone_name_prefix.strip(), smart_map=settings.auto_map_synonyms,
               is_cc5=operators.is_rig_cc5(arm, settings)) for name in packet["bones"]]
    matched = {pb.name: pb for pb in matched if pb is not None}
    if not matched:
        raise ValueError("Nenhum osso correspondente; confira o rig de destino")
    # Use the real cursor, never the receiver's cached last frame.
    if sync_frame or record:
        context.scene.frame_set(int(packet["frame"]))
    if record and arm.animation_data and arm.animation_data.action and arm.animation_data.action.users > 1:
        arm.animation_data.action = arm.animation_data.action.copy()
    if settings.pose_mapping_mode == 'GLOBAL_DELTA':
        conversion = (Matrix.Rotation(1.5707963267948966, 4, 'X')
                      if settings.coord_conversion == 'Y_UP_TO_Z_UP' else Matrix.Identity(4))
        conv_q = conversion.to_quaternion()
        arm_inv = arm.matrix_world.inverted()
        by_name = {}
        for name, transform in packet['bones'].items():
            pb = operators.find_matching_bone(arm.pose.bones, name, settings.bone_name_prefix.strip(),
                 smart_map=settings.auto_map_synonyms, is_cc5=operators.is_rig_cc5(arm, settings))
            if pb:
                if 'delta_rot' not in transform or 'g_pos' not in transform:
                    raise ValueError('Same Rig requer delta_rot e g_pos; use Local FK para este emissor')
                by_name[pb.name] = transform
        def depth(pb):
            return len(pb.parent_recursive)
        last_depth = -1
        for pb in sorted(matched.values(), key=depth):
            if depth(pb) != last_depth:
                context.view_layer.update()
                last_depth = depth(pb)
            tf = by_name[pb.name]
            delta = conv_q @ Quaternion(tf['delta_rot']).normalized() @ conv_q.inverted()
            rest_world = arm.matrix_world @ pb.bone.matrix_local
            rotation = delta @ rest_world.to_quaternion()
            position = conversion @ (Vector(tf['g_pos']) * settings.translation_scale)
            world = Matrix.LocRotScale(position, rotation, rest_world.to_scale())
            pb.rotation_mode = 'QUATERNION'
            pb.matrix = arm_inv @ world
        context.view_layer.update()
        settings.matched_bones_count = len(matched)
        settings.total_incoming_bones = len(packet['bones'])
    else:
        operators.apply_packet_to_rig(packet, arm, settings, record=False, sync_frame=True, context=context)
    if record:
        for pb in matched.values():
            pb.keyframe_insert(data_path="rotation_quaternion", frame=packet["frame"], group=pb.name)
            pb.keyframe_insert(data_path="location", frame=packet["frame"], group=pb.name)
    context.view_layer.update()
    return len(matched)


class CASCADEUR_OT_fetch_keyframe(bpy.types.Operator):
    bl_idname = "cascadeur.fetch_keyframe"
    bl_label = "Buscar pose e gravar keyframe"
    bl_description = "Busca a pose do frame atual do Blender no Cascadeur e grava chaves nesse mesmo frame"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.cascadeur_live_link
        arm = settings.target_armature or context.active_object
        if not arm or arm.type != 'ARMATURE':
            self.report({'ERROR'}, "Selecione o rig de destino")
            return {'CANCELLED'}
        if settings.is_connected:
            self.report({'ERROR'}, "Pare o Live Link antes de buscar um keyframe")
            return {'CANCELLED'}
        try:
            packet = request_pose(settings.host, settings.cascadeur_receive_port, context.scene.frame_current)
            count = commit_pose(packet, arm, settings, context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        settings.target_armature = arm
        settings.status_message = f"Keyframe {packet['frame']}: {count} ossos gravados"
        self.report({'INFO'}, settings.status_message)
        return {'FINISHED'}


def register():
    bpy.utils.register_class(CASCADEUR_OT_fetch_keyframe)


def unregister():
    bpy.utils.unregister_class(CASCADEUR_OT_fetch_keyframe)
