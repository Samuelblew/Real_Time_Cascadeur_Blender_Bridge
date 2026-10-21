import bpy,sys,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
import cascadeur_live_link as addon
addon.register()
from cascadeur_live_link import live_sync as sync
bpy.ops.object.armature_add()
arm=bpy.context.object
arm.data.bones[0].name='Hips'
s=bpy.context.scene.cascadeur_live_link
s.target_armature=arm
s.pose_mapping_mode='LOCAL_ROT'
s.coord_conversion='DIRECT'
s.translation_scale=1
s.use_rest_pose_calibration=False
def message(frames,rev):
    return {'type':'key_sync','source':'test','revision':rev,'bone_frames':{'Hips':frames},
            'entries':[{'frame':f,'bones':{'Hips':{'rot':[1,0,0,0],'pos':[f/100,0,0]}}} for f in frames]}
sync.reset(arm)
sync.apply_keys(message([10,30],'one'),arm,s,bpy.context)
assert sorted(sync.fingerprints(arm))==[10,30]
sync.apply_keys(message([10,40],'two'),arm,s,bpy.context)
assert sorted(sync.fingerprints(arm))==[10,40]
sync.apply_keys(message([40],'three'),arm,s,bpy.context)
assert sorted(sync.fingerprints(arm))==[40]
assert arm.animation_data.action.name=='Cascadeur Sync'
(root/'sparse_sync_results.json').write_text(json.dumps({'create':True,'move':True,'delete':True,'no_extra_keys':True}))
print('SPARSE_SYNC_PASSED')
