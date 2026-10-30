"""Small explicit previews for routing; file metadata never proves correctness."""
import hashlib
from pathlib import PurePosixPath
from v9_runtime.representation import bounded_text

ARTIFACT_SUFFIXES = {'.step', '.stp', '.brep', '.stl', '.obj', '.ply', '.iges', '.igs',
                     '.wav', '.mp3', '.flac', '.ogg', '.mid', '.midi', '.npy', '.npz',
                     '.pkl', '.pickle', '.pdf', '.zip', '.gz', '.parquet'}


def artifact_excerpt(path, data, limit=4000):
    row = {'path': path, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if PurePosixPath(path).suffix.lower() in ARTIFACT_SUFFIXES:
        return {**row, 'kind': 'artifact_metadata', 'content_omitted': True,
                'note': 'Metadata does not establish task correctness. Use recorded requirement checks; if insufficient, request a focused executable check or targeted read of this original file.'}
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return {**row, 'kind': 'binary_metadata', 'content_omitted': True,
                'note': 'Binary content omitted. Correctness requires actual requirement checks; the file remains available for targeted inspection.'}
    return {**row, 'kind': 'text_preview', **bounded_text(text, max(0, limit)),
            'note': 'This is a bounded preview. Do not infer absent content from truncation; request a focused read or executable check when required.'}
