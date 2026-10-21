import bpy,sys,json,time
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
import cascadeur_live_link as addon
addon.register()
from cascadeur_live_link import live_sync as sync
exec(compile((root/'tests/test_sparse_sync.py').read_text().replace('addon.register()',''),str(root/'tests/test_sparse_sync.py'),'exec'))
# Stress deletion of consecutive keys: remove from end, never hold stale RNA references.
sync.reset(arm)
sync.apply_keys(message(list(range(124)),'many'),arm,s,bpy.context)
sync.apply_keys(message([0],'only-zero'),arm,s,bpy.context)
assert list(sync.fingerprints(arm))==[0]
assert sync.edited_frames({1:(('a',0,1),('a',1,2))},{1:(('a',0,1),)})==[]
assert sync.edited_frames({1:(('a',0,1),)},{1:(('a',0,2),)})==[1]
# A failed edit gets one attempt until something changes or the user retries.
s.sync_blender_keys=True
sync.reset(arm)
arm.pose.bones['Hips'].location.x=1
arm.pose.bones['Hips'].keyframe_insert(data_path='location',frame=0)
calls=[]
def fail(*args):
    calls.append(1)
    raise ValueError('simulated rejection')
sync.send_frames=fail
for _ in range(4):
    sync._last_poll=0
    sync.poll_local(arm,s,bpy.context)
assert len(calls)==1,len(calls)
(root/'delete_regression_results.json').write_text(json.dumps({'124_to_1_key':True,'deletion_does_not_send_pose':True,'changed_value_sends':True,'failure_attempts':len(calls)}))
print('DELETE_REGRESSION_PASSED')
