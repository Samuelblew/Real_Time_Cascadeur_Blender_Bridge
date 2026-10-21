"""Main-thread sampling of sparse Cascadeur track keys and live pose transport."""
import base64
import hashlib
import json
import time
import zlib

_last_scan = 0
_last_send = 0
_cached = None
_revision = None
_pending = None
_active_scene = None


def send_message(sock, host, port, message):
    raw = json.dumps(message, separators=(',', ':'), sort_keys=True).encode()
    packed = base64.b64encode(zlib.compress(raw, 3)).decode()
    token = hashlib.sha256(raw).hexdigest()
    parts = [packed[i:i+40000] for i in range(0, len(packed), 40000)]
    for index, part in enumerate(parts):
        sock.sendto(json.dumps({'type':'bridge_chunk', 'id':token, 'part':index,
                     'parts':len(parts), 'data':part}).encode(), (host, port))


def publish(streamer, scene, data):
    global _last_scan, _last_send, _cached, _revision, _pending, _active_scene
    if _active_scene is not scene:
        _active_scene = scene
        _pending = _cached = _revision = None
        _last_scan = _last_send = 0
    now = time.monotonic()
    if _pending is None and now - _last_scan >= 0.6:
        _last_scan = now
        mv = scene.model_viewer()
        bv, lv = mv.behaviour_viewer(), scene.layers_viewer()
        key_frames = {}
        identity = []
        for jid in bv.get_behaviours('Joint'):
            owner = bv.get_behaviour_owner(jid)
            name = mv.get_object_name(owner)
            identity.append(name + ':' + str(owner))
            lid = lv.layer_id_by_obj_id_or_null(owner)
            key_frames[name] = list(lv.layer(lid).key_frame_indices()) if not lid.is_null() else []
        frames = sorted({f for keys in key_frames.values() for f in keys})
        _pending = {'source':hashlib.sha256((str(id(scene))+'|'.join(sorted(identity))).encode()).hexdigest(),
                    'bone_frames':key_frames,'frames':iter(frames),'entries':[]}
    if _pending is not None:
        deadline = time.perf_counter()+0.004
        while time.perf_counter()<deadline:
            frame = next(_pending['frames'], None)
            if frame is None:
                break
            snapshot = streamer.get_cascadeur_joint_data(scene, frame)
            selected = {name:tf for name,tf in snapshot['bones'].items() if frame in _pending['bone_frames'].get(name, [])}
            _pending['entries'].append({'frame':frame, 'bones':selected})
        else:
            frame = -1
        if frame is None:
        # Every snapshot is complete: dropped packets cannot leave an incomplete deletion/move.
            message = {'type':'key_sync', 'source':_pending['source'],
                       'bone_frames':_pending['bone_frames'], 'entries':_pending['entries']}
            revision = hashlib.sha256(json.dumps(message, sort_keys=True).encode()).hexdigest()
            changed = revision != _revision
            _cached, _revision, _pending = message, revision, None
            _cached['revision'] = revision
            _last_scan = now
            if changed:
                _last_send = 0
    if _cached is not None and now - _last_send >= 2:
        send_message(streamer.sock, streamer.host, streamer.port, _cached)
        _last_send = now
    data['bridge_protocol'] = 2
    data['key_revision'] = _revision
    data['bridge_source'] = (_cached or _pending or {}).get('source')
