bl_info = {
    "name": "Cascadeur Real-Time Live Link",
    "author": "Carlos Barreto, Antigravity",
    "version": (1, 4, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > CEB",
    "description": "Stream character animation from Cascadeur to Blender armatures in real time via UDP.",
    "category": "Animation",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    StringProperty,
    IntProperty,
    BoolProperty,
    EnumProperty,
    PointerProperty,
    FloatProperty,
    CollectionProperty,
)

from . import operators
from . import panel
from . import profiles


def update_rig_type(self, context):
    if self.character_rig_type == "CC5":
        self.translation_scale = 0.01
        self.pose_mapping_mode = "GLOBAL_AIM"
        self.apply_root_only_translation = True
    elif self.character_rig_type == "CASCADEUR_MIXAMO":
        self.translation_scale = 1.0
        self.pose_mapping_mode = "GLOBAL_AIM"
        self.apply_root_only_translation = True
    elif self.character_rig_type == "AUTO":
        from .operators import is_rig_cc5
        if self.target_armature and is_rig_cc5(self.target_armature, self):
            self.translation_scale = 0.01
        else:
            self.translation_scale = 1.0


def update_target_armature(self, context):
    if self.is_connected:
        bpy.ops.cascadeur.live_link_stop()
        self.status_message = 'Rig alterado: confira a cena Cascadeur e reconecte'


class CascadeurLiveLinkSettings(bpy.types.PropertyGroup):
    rig_profile: EnumProperty(name='Perfil de ossos',items=profiles.PROFILES,default='EXPORTED')
    mapping_only: BoolProperty(name='Somente pares habilitados',default=False,update=profiles.edited)
    bone_mapping: CollectionProperty(type=profiles.BonePair)

    sync_cascadeur_keys: BoolProperty(name="Sincronizar chaves Cascadeur → Blender", default=True)
    sync_blender_keys: BoolProperty(name="Enviar ao inserir/alterar chaves no Blender", default=True)
    key_range_start: IntProperty(name="Inicio", default=0)
    key_range_end: IntProperty(name="Fim", default=250)
    host: StringProperty(
        name="Host Address",
        description="IP address to listen on for Cascadeur streaming packets",
        default="127.0.0.1",
    )
    port: IntProperty(
        name="UDP Port",
        description="UDP port to listen on",
        default=9999,
        min=1024,
        max=65535,
    )
    cascadeur_receive_port: IntProperty(
        name="Cascadeur Receive Port",
        description="UDP port where Cascadeur listens for incoming poses from Blender",
        default=9998,
        min=1024,
        max=65535,
    )
    send_pose_target_frame: EnumProperty(
        name="Target Frame",
        description="Target frame in Cascadeur to apply the transmitted pose to",
        items=[
            ("CURRENT", "Cascadeur Active Frame", "Apply pose directly to Cascadeur's currently active playhead frame"),
            ("BLENDER", "Match Blender Frame", "Apply pose to Cascadeur matching Blender's current timeline frame number"),
        ],
        default="BLENDER",
    )
    target_armature: PointerProperty(
        name="Target Armature",
        type=bpy.types.Object,
        description="Blender Armature object to animate with incoming Cascadeur data",
        poll=lambda self, obj: obj.type == 'ARMATURE',
        update=update_target_armature,
    )

    character_rig_type: EnumProperty(
        name="Rig Type",
        description="Character rig preset and naming convention",
        items=[
            ("AUTO", "Auto Detect", "Automatically detect rig type from bone names (CC_Base, Mixamo, etc.)"),
            ("CASCADEUR_MIXAMO", "Cascadeur / Mixamo", "Standard T-Pose rigs (Cascadeur native, Mixamo, Unreal Mannequin)"),
            ("CC5", "Character Creator (CC4 / CC5)", "Reallusion Character Creator rigs (A-Pose, CC_Base bones, centimeter scale)"),
        ],
        default="AUTO",
        update=update_rig_type,
    )
    pose_mapping_mode: EnumProperty(
        name="Pose Mode",
        description="Method used to calculate bone rotations in Blender",
        items=[
            ("GLOBAL_MATRIX", "Same Exported Rig / Global Transforms", "Use original joint world orientations; suitable for the original Blender export rig"),
            ("GLOBAL_DELTA", "Same Rig / Global Delta", "Preserve streamed joint transforms on the same exported skeleton"),
            ("GLOBAL_AIM", "Global Position Aim (Recommended)", "Solve bone rotations directly from 3D joint positions (ignores bone roll / rest pose mismatch)"),
            ("LOCAL_ROT", "Local FK Hierarchy", "Apply local bone rotation quaternions directly"),
        ],
        default="GLOBAL_DELTA",
    )
    coord_conversion: EnumProperty(
        name="Coordinate Conversion",
        description="Coordinate space conversion from Cascadeur to Blender",
        items=[
            ("Y_UP_TO_Z_UP", "Cascadeur Y-Up to Blender Z-Up (Standard)", "Convert Cascadeur Y-Up world to Blender Z-Up world on root"),
            ("DIRECT", "Direct (Pass-Through)", "Pass transforms directly without root orientation conversion"),
            ("Z_UP", "Cascadeur Z-Up to Blender Z-Up", "Pass-through for Z-Up Cascadeur scenes"),
        ],
        default="Y_UP_TO_Z_UP",
    )
    translation_scale: FloatProperty(
        name="Translation Scale",
        description="Scale multiplier applied to root/pelvis translation (e.g. 0.01 for Cascadeur cm to Blender m)",
        default=0.01,
        min=0.00001,
        max=10000.0,
        step=0.01,
        precision=4,
    )
    auto_calc_scale: BoolProperty(
        name="Auto-Calculate Scale",
        description="Automatically determine the translation scale ratio by comparing character proportions between Cascadeur and Blender",
        default=False,
    )
    translation_mode: EnumProperty(
        name="Translation Reference",
        description="How pelvis translation is evaluated",
        items=[
            ("ABSOLUTE", "Absolute Scaled", "Apply scaled world coordinates directly"),
            ("RELATIVE", "Relative to Rest Pose", "Add scaled displacement relative to the initial frame/rest pose"),
        ],
        default="ABSOLUTE",
    )
    use_rest_pose_calibration: BoolProperty(
        name="Use Rest Pose Calibration",
        description="Calculate bone rotation deltas relative to calibrated rest poses, eliminating roll and mirroring discrepancies",
        default=True,
    )
    is_rest_pose_calibrated: BoolProperty(
        name="Is Calibrated",
        description="True when rest pose calibration data has been successfully stored",
        default=False,
    )
    calibrated_bone_count: IntProperty(
        name="Calibrated Bones",
        description="Number of bones currently calibrated for delta retargeting",
        default=0,
    )
    rest_pose_data: StringProperty(
        name="Rest Pose Data",
        description="Internal JSON storage for calibrated rest pose data",
        default="",
    )
    record_animation: BoolProperty(
        name="Record Keyframes",
        description="Automatically insert keyframes on active frames as data arrives",
        default=False,
    )
    sync_timeline_frame: BoolProperty(
        name="Sync Frame Number",
        description="Move Blender's timeline cursor to the frame number sent by Cascadeur",
        default=True,
    )
    bone_name_prefix: StringProperty(
        name="Prefix Filter",
        description="Optional prefix to strip from Cascadeur joint names or add to Blender bones",
        default="",
    )
    update_rate_fps: IntProperty(
        name="Receiver Polling Rate (Hz)",
        description="Internal timer check frequency in Hz",
        default=60,
        min=10,
        max=240,
    )
    status_message: StringProperty(
        name="Status",
        description="Current connection status",
        default="Disconnected",
    )
    is_connected: BoolProperty(
        name="Is Connected",
        description="True when Live Link modal operator is running",
        default=False,
    )
    packets_received: IntProperty(
        name="Packets Received",
        description="Total packet count received in current session",
        default=0,
    )
    last_frame_received: IntProperty(
        name="Last Frame",
        description="Last animation frame received from Cascadeur",
        default=0,
    )
    stream_fps: FloatProperty(
        name="Stream FPS",
        description="Animation packets received per second from Cascadeur",
        default=0.0,
        precision=1,
    )
    viewport_fps: FloatProperty(
        name="Viewport FPS",
        description="Current render/redraw framerate of Blender 3D Viewport",
        default=0.0,
        precision=1,
    )
    matched_bones_count: IntProperty(
        name="Matched Bones",
        description="Number of bones matched with target armature in last packet",
        default=0,
    )
    total_incoming_bones: IntProperty(
        name="Incoming Bones",
        description="Total number of bones sent in last packet",
        default=0,
    )
    apply_root_only_translation: BoolProperty(
        name="Root Only Translation",
        description="Apply translation only to root/hips bones to avoid collapsing limbs, while applying rotation to all bones",
        default=True,
    )
    auto_map_synonyms: BoolProperty(
        name="Smart Bone Matching",
        description="Auto-match common naming conventions (e.g. Hips <-> Pelvis, strip mixamorig prefixes)",
        default=True,
    )
    last_matched_bone_names: StringProperty(
        name="Matched Names Sample",
        default="",
    )
    last_unmatched_bone_names: StringProperty(
        name="Unmatched Names Sample",
        default="",
    )
    show_viewport_overlay: BoolProperty(
        name="Show Viewport Overlay",
        description="Display real-time HUD status indicator in the 3D Viewport while Live Link is running",
        default=True,
    )


classes = (
    profiles.BonePair,
    CascadeurLiveLinkSettings,
)


@persistent
def reset_connection_after_load(_):
    operators._stop_event.set()
    if bpy.app.timers.is_registered(operators._tick_process_packets):
        bpy.app.timers.unregister(operators._tick_process_packets)
    operators._timer_registered = False
    from . import live_sync, overlay
    live_sync.reset()
    overlay.unregister_overlay()
    for scene in bpy.data.scenes:
        scene.cascadeur_live_link.is_connected = False
        scene.cascadeur_live_link.status_message = 'Clique Start Live Link para conectar esta cena'


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.cascadeur_live_link = PointerProperty(type=CascadeurLiveLinkSettings)

    operators.register()
    from . import keyframe_bridge
    keyframe_bridge.register()
    from . import live_sync
    live_sync.register()
    profiles.register()
    panel.register()
    bpy.app.handlers.load_post.append(reset_connection_after_load)


def unregister():
    from . import overlay
    overlay.unregister_overlay()
    if reset_connection_after_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(reset_connection_after_load)

    from . import keyframe_bridge
    from . import live_sync
    live_sync.unregister()
    keyframe_bridge.unregister()
    panel.unregister()
    profiles.unregister()
    operators.unregister()

    del bpy.types.Scene.cascadeur_live_link

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
