from v19_runtime.policy import *

def preparation_images(phase, images):
    return [] if phase == "worker-discovery" or phase.startswith("worker-preparation-") else images
