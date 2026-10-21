"""Blender regression: exported joints retain absolute transforms and local round trip."""
import bpy,sys,json,math
from pathlib import Path
from mathutils import Matrix,Vector,Quaternion
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cascadeur_live_link as addon
addon.register()
from cascadeur_live_link import keyframe_bridge as bridge
bpy.ops.object.armature_add()
arm=bpy.context.object
arm.name='Export rig'
bpy.ops.object.mode_set(mode='EDIT')
root=arm.data.edit_bones[0]
root.name='root';root.head=(0,0,0);root.tail=(0,1,0)
child=arm.data.edit_bones.new('child');child.head=(0,1,0);child.tail=(0,2,0);child.parent=root;child.use_connect=True
bpy.ops.object.mode_set(mode='OBJECT')
arm.matrix_world=Matrix.Scale(1.387166,4)
rest={b.name:b.matrix_local.copy() for b in arm.data.bones}
assert bridge.prepare_joint_translation(arm,bpy.context)==1
assert arm.data.bones['child'].parent.name=='root'
assert all(max(abs(a-b) for r1,r2 in zip(rest[n],arm.data.bones[n].matrix_local) for a,b in zip(r1,r2))<1e-6 for n in rest)
s=bpy.context.scene.cascadeur_live_link
s.target_armature=arm;s.pose_mapping_mode='GLOBAL_MATRIX';s.translation_scale=.01;s.auto_map_synonyms=False
q=Quaternion((0,0,1),.3)
packet={'frame':7,'bones':{'root':{'g_pos':[100,50,-30],'g_rot':list(q)},'child':{'g_pos':[120,70,-40],'g_rot':list(q)}}}
bridge.commit_pose(packet,arm,s,bpy.context,record=True)
c=Matrix.Rotation(math.pi/2,4,'X')
for name,tf in packet['bones'].items():
    world=arm.matrix_world@arm.pose.bones[name].matrix
    assert (world.translation-c@ (Vector(tf['g_pos'])*.01)).length<1e-5
    angle=world.to_quaternion().rotation_difference(c.to_quaternion()@q).angle
    assert min(angle,abs(2*math.pi-angle))<.001
out=bridge.capture_pose(arm,s)
for name,data in out.items():
    assert (Vector(data['g_pos'])-Vector(packet['bones'][name]['g_pos'])).length<.001
    pb=arm.pose.bones[name]
    local=Matrix.LocRotScale(Vector(data['export_local_pos']),Quaternion(data['export_local_rot']),Vector((1,1,1)))
    reconstructed=pb.parent.matrix@local if pb.parent else local
    assert max(abs(a-b) for r1,r2 in zip(reconstructed,pb.matrix) for a,b in zip(r1,r2))<1e-5
assert bridge.prepare_joint_translation(arm,bpy.context)==0
print('EXPORTED_RIG_TRANSFORMS_PASSED')

# Retarget mode keeps target proportions when only root translation is enabled.
s.pose_mapping_mode='GLOBAL_DELTA';s.apply_root_only_translation=True
for tf in packet['bones'].values():tf['delta_rot']=[1,0,0,0]
packet['bones']['child']['g_pos']=[9999,9999,9999]
bridge.commit_pose(packet,arm,s,bpy.context,record=False)
root_world=arm.matrix_world@arm.pose.bones['root'].matrix
child_world=arm.matrix_world@arm.pose.bones['child'].matrix
assert abs((child_world.translation-root_world.translation).length-1.387166)<1e-5
print('RETARGET_PROPORTIONS_PASSED')
