from .start_streamer import cascadeur_streamer


def name():
    return "Blender Live Link.Start Keyframe Bridge"


def description():
    return "Receive explicit frame/pose requests from Blender without continuous streaming"


def run(scene):
    cascadeur_streamer.streamer.stop()
    message = cascadeur_streamer.start_pose_receiver()
    scene.info(str(message))
