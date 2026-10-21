import bpy


class VIEW3D_PT_cascadeur_live_link(bpy.types.Panel):
    bl_label = "Cascadeur Live Link"
    bl_idname = "VIEW3D_PT_cascadeur_live_link"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        box = layout.box()
        box.label(text=f"Keyframe Bridge | Frame {context.scene.frame_current}", icon="KEY_HLT")
        box.operator("cascadeur.timeline_sync_active_frame", text="Enviar frame ao Cascadeur")
        box.operator("cascadeur.fetch_keyframe", text="Buscar pose e gravar keyframe", icon="IMPORT")
        box.operator("cascadeur.send_pose_to_cascadeur", text="Enviar pose ao mesmo frame", icon="EXPORT")
        box.label(text="Live Link inicia o preview nos dois lados", icon="INFO")
        box = layout.box()
        box.label(text="Pose ao vivo + Keyframes", icon="ACTION")
        box.prop(settings, 'sync_cascadeur_keys')
        box.prop(settings, 'sync_blender_keys')
        box.operator('cascadeur.resume_preview')
        row = box.row(align=True)
        row.prop(settings, 'key_range_start')
        row.prop(settings, 'key_range_end')
        box.operator('cascadeur.send_key_range', icon='EXPORT')

        # --- Status Box ---
        status_box = layout.box()
        col = status_box.column(align=True)

        if settings.is_connected:
            row = col.row(align=True)
            row.label(text=f"Status: {settings.status_message}", icon="RECORD_ON")
            row = col.row(align=True)
            row.label(text=f"Packets: {settings.packets_received} | Frame: {settings.last_frame_received}", icon="TIME")
            row = col.row(align=True)
            row.label(text=f"Recv: {settings.stream_fps:.1f} FPS | View: {settings.viewport_fps:.1f} FPS", icon="FORWARD")

            # Bone matching diagnostic feedback
            if settings.total_incoming_bones > 0:
                if settings.matched_bones_count > 0:
                    row = col.row(align=True)
                    row.label(
                        text=f"Matched Bones: {settings.matched_bones_count} / {settings.total_incoming_bones}",
                        icon="CHECKMARK",
                    )
                    if settings.last_matched_bone_names:
                        sub = col.column(align=True)
                        sub.scale_y = 0.8
                        sub.label(text=f"Sample: {settings.last_matched_bone_names}")
                else:
                    err_box = col.box()
                    err_box.alert = True
                    err_box.label(text=f"0 / {settings.total_incoming_bones} Bones Matched!", icon="ERROR")
                    err_box.label(text="Incoming names don't match rig bones:")
                    sub = err_box.column(align=True)
                    sub.scale_y = 0.8
                    sub.label(text=f"{settings.last_unmatched_bone_names}")
                    err_box.label(text="Check Prefix Filter or Smart Bone Matching.")
        else:
            row = col.row(align=True)
            row.label(text=f"Status: {settings.status_message}", icon="RADIOBUT_OFF")

        # --- Connection Buttons ---
        col = layout.column(align=True)
        if not settings.is_connected:
            col.operator(
                "cascadeur.live_link_start",
                text="Start Live Link",
                icon="PLAY",
            )
        else:
            col.operator(
                "cascadeur.live_link_stop",
                text="Stop Live Link",
                icon="CANCEL",
            )
        col.prop(settings, "show_viewport_overlay", text="Viewport HUD Overlay")

        # --- Target Armature Section ---
        box = layout.box()
        box.label(text="Target Character Rig", icon="ARMATURE_DATA")
        col = box.column(align=True)
        col.prop(settings, "target_armature", text="")
        col.prop(settings, "character_rig_type", text="Rig Type")
        col.label(text='Addon 1.4.0 | Perfis FK / deformacao')
        col.label(text='Perfis e pares: abra Perfil de personagem abaixo')

        if settings.character_rig_type == "CC5":
            col.label(text="Profile: CC5 (Pelvis-Only Trans, 0.01 Scale)", icon="INFO")

        if settings.target_armature:
            arm_obj = settings.target_armature
            if arm_obj.type == 'ARMATURE' and arm_obj.pose:
                num_bones = len(arm_obj.pose.bones)
                col.label(text=f"Available Pose Bones: {num_bones}", icon="BONE_DATA")
                from . import operators
                pelvis_pb = operators.get_pelvis_bone(arm_obj.pose.bones)
                if pelvis_pb:
                    col.label(text=f"Pelvis Translation Bone: {pelvis_pb.name}", icon="CHECKMARK")
            col.separator()
            col.operator(
                "cascadeur.send_pose_to_cascadeur",
                text="Send Pose to Cascadeur",
                icon="EXPORT",
            )
        else:
            col.label(text="Select an Armature object to receive pose", icon="INFO")


class VIEW3D_PT_cascadeur_network(bpy.types.Panel):
    bl_label = "Network Configuration"
    bl_idname = "VIEW3D_PT_cascadeur_network"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        col = layout.column(align=True)
        col.prop(settings, "host", text="Host")
        col.prop(settings, "port", text="Listen Port (from Cascadeur)")
        col.prop(settings, "cascadeur_receive_port", text="Send Port (to Cascadeur)")
        col.prop(settings, "update_rate_fps", text="Timer Poll (Hz)")


class VIEW3D_PT_cascadeur_mapping(bpy.types.Panel):
    bl_label = "Coordinate & Bone Mapping"
    bl_idname = "VIEW3D_PT_cascadeur_mapping"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        col = layout.column(align=True)
        col.prop(settings, "pose_mapping_mode", text="Pose Mode")
        if settings.pose_mapping_mode == 'GLOBAL_MATRIX':
            col.operator('cascadeur.prepare_joint_translation',icon='BONE_DATA')
        col.prop(settings, "coord_conversion", text="Conversion")
        col.prop(settings, "auto_map_synonyms", text="Smart Bone Matching")
        col.prop(settings, "apply_root_only_translation", text="Root Only Translation")
        col.prop(settings, "bone_name_prefix", text="Prefix Filter")


class VIEW3D_PT_cascadeur_calibration(bpy.types.Panel):
    bl_label = "Rest Pose / T-Pose Calibration"
    bl_idname = "VIEW3D_PT_cascadeur_calibration"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        col = layout.column(align=True)
        col.prop(settings, "use_rest_pose_calibration", text="Use Rest Pose Calibration")

        row = col.row(align=True)
        if settings.is_rest_pose_calibrated:
            row.label(text=f"✓ Calibrated ({settings.calibrated_bone_count} bones)", icon="CHECKMARK")
        else:
            row.label(text="Not Calibrated", icon="INFO")

        row = col.row(align=True)
        row.operator("cascadeur.calibrate_rest_pose", text="Calibrate from Stream", icon="EYEDROPPER")
        if settings.is_rest_pose_calibrated:
            row.operator("cascadeur.clear_calibration", text="Clear", icon="X")


class VIEW3D_PT_cascadeur_scaling(bpy.types.Panel):
    bl_label = "Translation & Scaling (Root Motion)"
    bl_idname = "VIEW3D_PT_cascadeur_scaling"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        col = layout.column(align=True)
        col.prop(settings, "translation_scale", text="Translation Scale")
        col.prop(settings, "auto_calc_scale", text="Auto-Calculate Scale")
        col.prop(settings, "translation_mode", text="Mode")

        row = col.row(align=True)
        op1 = row.operator("cascadeur.set_scale_preset", text="0.01 (cm to m)")
        op1.preset_value = 0.01
        op2 = row.operator("cascadeur.set_scale_preset", text="0.1 (dm to m)")
        op2.preset_value = 0.1
        op3 = row.operator("cascadeur.set_scale_preset", text="1.0 (Direct)")
        op3.preset_value = 1.0

        col.operator("cascadeur.detect_scale", text="Calculate Scale from Rig", icon="TRACKING_REFINE_FORWARDS")


class VIEW3D_PT_cascadeur_recording(bpy.types.Panel):
    bl_label = "Animation Recording"
    bl_idname = "VIEW3D_PT_cascadeur_recording"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        col = layout.column(align=True)
        col.prop(settings, "record_animation", text="Record Keyframes", icon="REC")
        col.prop(settings, "sync_timeline_frame", text="Sync Current Frame")


class VIEW3D_PT_cascadeur_utilities(bpy.types.Panel):
    bl_label = "Utilities"
    bl_idname = "VIEW3D_PT_cascadeur_utilities"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        box = layout.box()
        box.label(text="Send Pose to Cascadeur", icon="EXPORT")
        box.prop(settings, "send_pose_target_frame", text="Target")
        box.operator("cascadeur.send_pose_to_cascadeur", text="Send Active Pose", icon="EXPORT")

        layout.separator()
        row = layout.row(align=True)
        row.operator("cascadeur.reset_pose", text="Reset Pose", icon="FILE_REFRESH")
        row.operator("cascadeur.clear_keyframes", text="Clear Keyframes", icon="TRASH")


class VIEW3D_PT_cascadeur_timeline(bpy.types.Panel):
    bl_label = "Cascadeur Timeline & Playback"
    bl_idname = "VIEW3D_PT_cascadeur_timeline"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CEB"
    bl_parent_id = "VIEW3D_PT_cascadeur_live_link"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.cascadeur_live_link

        box = layout.box()
        box.label(text="Remote Transport Controls", icon="PLAY")

        # Transport buttons row
        row = box.row(align=True)
        row.scale_y = 1.3
        row.operator("cascadeur.timeline_to_first", text="", icon="REW")
        row.operator("cascadeur.timeline_prev_key", text="", icon="PREV_KEYFRAME")
        row.operator("cascadeur.timeline_prev_frame", text="", icon="FRAME_PREV")
        row.operator("cascadeur.timeline_play_pause", text="Play / Pause", icon="PLAY")
        row.operator("cascadeur.timeline_next_frame", text="", icon="FRAME_NEXT")
        row.operator("cascadeur.timeline_next_key", text="", icon="NEXT_KEYFRAME")
        row.operator("cascadeur.timeline_to_last", text="", icon="FF")

        col = box.column(align=True)
        col.operator("cascadeur.timeline_sync_active_frame", text="Sync Current Frame to Cascadeur", icon="TIME")



class VIEW3D_PT_cascadeur_profiles(bpy.types.Panel):
    bl_label='Perfil de personagem'
    bl_idname='VIEW3D_PT_cascadeur_profiles'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='CEB'
    bl_parent_id='VIEW3D_PT_cascadeur_live_link'
    bl_options={'DEFAULT_CLOSED'}
    def draw(self,context):
        settings=context.scene.cascadeur_live_link
        col=self.layout.column(align=True)
        col.prop(settings,'rig_profile')
        col.operator('cascadeur.build_profile')
        col.prop(settings,'mapping_only')
        if settings.rig_profile in {'RIGIFY','ARP'}:
            col.label(text='Use ossos FK/deformacao; IK requer bake.')
        for i,pair in enumerate(settings.bone_mapping):
            row=col.row(align=True)
            row.prop(pair,'enabled',text='')
            row.prop(pair,'source',text='')
            if settings.target_armature:
                row.prop_search(pair,'target',settings.target_armature.data,'bones',text='')
            else:row.prop(pair,'target',text='')
            row.operator('cascadeur.remove_bone_pair',text='',icon='X').index=i
        col.operator('cascadeur.add_bone_pair',icon='ADD')
        row=col.row(align=True)
        row.operator('cascadeur.profile_file',text='Importar JSON').action='IMPORT'
        row.operator('cascadeur.profile_file',text='Salvar JSON').action='EXPORT'


classes = (
    VIEW3D_PT_cascadeur_live_link,
    VIEW3D_PT_cascadeur_profiles,
    VIEW3D_PT_cascadeur_timeline,
    VIEW3D_PT_cascadeur_network,
    VIEW3D_PT_cascadeur_mapping,
    VIEW3D_PT_cascadeur_calibration,
    VIEW3D_PT_cascadeur_scaling,
    VIEW3D_PT_cascadeur_recording,
    VIEW3D_PT_cascadeur_utilities,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
