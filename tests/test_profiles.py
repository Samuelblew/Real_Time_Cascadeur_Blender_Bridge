import bpy,sys,json,tempfile
from pathlib import Path
from mathutils import Matrix
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cascadeur_live_link as addon
addon.register()
from cascadeur_live_link import profiles,operators,keyframe_bridge as bridge
bpy.ops.object.armature_add();arm=bpy.context.object
arm.data.bones[0].name='DEF-upper_arm.L'
s=bpy.context.scene.cascadeur_live_link;s.target_armature=arm;s.rig_profile='RIGIFY'
operators._last_packet_received={'frame':1,'bones':{'LeftArm':{},'Unrelated':{}}}
assert bpy.ops.cascadeur.build_profile()=={'FINISHED'}
assert len(s.bone_mapping)==2 and s.bone_mapping[0].target=='DEF-upper_arm.L'
assert not s.bone_mapping[1].enabled
assert operators.find_matching_bone(arm.pose.bones,'LeftArm').name=='DEF-upper_arm.L'
assert operators.find_matching_bone(arm.pose.bones,'DEF-upper_arm.L') is None
out=bridge.capture_pose(arm,s);assert set(out)=={'LeftArm'}
path=str(Path(tempfile.gettempdir())/'bridge_profile_regression.json')
assert bpy.ops.cascadeur.profile_file(action='EXPORT',filepath=path)=={'FINISHED'}
s.bone_mapping.clear()
assert bpy.ops.cascadeur.profile_file(action='IMPORT',filepath=path)=={'FINISHED'}
assert s.bone_mapping[0].source=='LeftArm'
assert profiles.candidates('LeftUpLeg','METARIG')==['thigh.l']
assert profiles.candidates('LeftArm','ARP')==['arm_stretch.l']
# Preview retransmission must not reevaluate an unchanged pose; a frame change must.
s.sync_blender_keys=False;s.sync_cascadeur_keys=False;s.record_animation=False;s.sync_timeline_frame=False
operators._stop_event.clear()
packet={'frame':1,'bones':{'LeftArm':{'rot':[1,0,0,0],'pos':[0,0,0]}}}
calls=[]
original=operators.apply_packet_to_rig
operators.apply_packet_to_rig=lambda *a,**kw:calls.append(a[0]['frame'])
for frame in [1,1,2]:
    operators._packet_queue.put(dict(packet,frame=frame));operators._tick_process_packets()
operators.apply_packet_to_rig=original
assert calls==[1,2],calls
print('PROFILES_AND_DUPLICATE_PREVIEW_PASSED')
