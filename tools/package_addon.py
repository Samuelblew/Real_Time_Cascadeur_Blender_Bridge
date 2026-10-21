"""Build an installable Blender ZIP from source; excludes caches and local scenes."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
output = root / 'dist' / 'Cascadeur_Live_Sync_1_2_1.zip'
output.parent.mkdir(exist_ok=True)
with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
    for source in sorted((root / 'cascadeur_live_link').glob('*.py')):
        archive.write(source, source.relative_to(root).as_posix())
print(output)
