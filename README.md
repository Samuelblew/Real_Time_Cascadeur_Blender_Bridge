# Real Time Cascadeur Blender Bridge

Local pose and keyframe integration between **Blender 5.2** and **Cascadeur 2026.2**, based on [Blender_Cascadeur_RT by Carlos Barreto / Antigravity](https://github.com/carlosedubarreto/Blender_Cascadeur_RT).

Current version: **1.4.0**. This is a prototype validated with the same exported deformation skeleton in both applications. Character profiles provide editable mapping suggestions; they do not guarantee universal retargeting.

## Features

- **Cascadeur to Blender:** continuous pose preview, including pose changes within the same frame.
- **Cascadeur keyframes to Blender:** mirror key creation, value changes, moves, and deletion into the synchronized Action and bone groups.
- **Blender to Cascadeur:** automatically send poses when keys are inserted or their values change; manually send one pose or a range of keyed frames.
- Copy the original Action before modifying synchronized channels, with protection against feedback loops and accidental source changes.
- Handle outgoing keys beyond a Cascadeur timeline shortened by key deletion.
- Editable character profiles with JSON import/export and explicit bone selection.

## Installation: two separate components

**The Blender addon does not automatically install the Cascadeur scripts.** Install both components once.

### Blender

1. Download [Cascadeur_Live_Sync_1_4_0.zip](dist/Cascadeur_Live_Sync_1_4_0.zip).
2. In Blender 5.2, open **Edit > Preferences > Add-ons > Install from Disk**.
3. Select the ZIP and enable **Cascadeur Real-Time Live Link**.
4. Find the panel in the 3D viewport under **N > CEB**.

### Cascadeur

Download or clone the entire repository, keeping `cascadeur_scripts/` beside the installer. Close Cascadeur before installing.

Run this in PowerShell from the repository directory:

```powershell
.\install_cascadeur_menu.ps1 -CascadeurPath 'C:\Program Files\Cascadeur'
```

Replace the path with the directory containing `cascadeur.exe`. If that directory requires administrator access, run PowerShell as administrator. The installer does not change execution policies or download dependencies. It backs up an existing installation before copying scripts.

The destination is:

```text
CASCADEUR/resources/scripts/python/scripts/blender_live_link/
```

For manual installation, create that directory and copy the `.py` files from `cascadeur_scripts/cascadeur_live_link_package/`, plus `cascadeur_scripts/cascadeur_streamer.py` and `cascadeur_scripts/live_sync.py`.

Reopen Cascadeur and run **Scripts > Blender Live Link > Start Streamer**. REA, MCP, and external Python packages are not required to use the bridge. It uses Cascadeur's bundled `csc` API and Qt runtime.

## First connection

1. Open the intended character skeleton in both applications. Import the character first through your usual bridge or FBX workflow; Live Link does not import the model.
2. In Cascadeur, run **Scripts > Blender Live Link > Start Streamer**.
3. In Blender, open **N > CEB** and select **Target Character Rig**.
4. Choose the appropriate mapping mode as described below. Check the coordinate conversion and unit scale. The Maya export used **Y-Up to Z-Up** and **0.01** for centimeter data. Use matching scene frame rates.
5. Enable the desired synchronization options and click **Start Live Link**.

When editing a pose in Blender, incoming preview pauses to protect the local edit. Insert a key to send the pose, or click **Retomar preview do Cascadeur** (resume preview) to continue receiving without recording that edit.

**To delete keys in both applications, delete them in Cascadeur.** Deleting keys in Blender does not delete source keys in this version. Moving a Blender key sends the pose at the new frame but does not remove the old Cascadeur key.

Additional documentation: [controls and new characters, in Portuguese](docs/GUIA_USO_E_PERSONAGENS.md), [validation notes](docs/VALIDATION.md), and [detailed Maya rig diagnosis and character compatibility, in Portuguese](docs/MAYA_RIG_AND_CHARACTER_COMPATIBILITY.md).

## Original exported skeleton

For the same original skeleton exported from Blender, explicitly select its deformation armature and choose **Same Exported Rig / Global Transforms**. This mode uses the original global joint orientations without adding Blender's rest orientation again. Use exact names and disable synonym matching when names already match.

With Live Link stopped, click **Permitir translacao dos joints** (allow joint translation) once. This disables **Connected** only on the chosen armature, preserving bone parents, rest transforms, and skin weights. Connected forces a child's head onto its parent's tail, preventing some Cascadeur joint translations from being reproduced. Save a copy before preparing a new character.

**Same Rig / Global Delta** remains available for cases where applying source rotation deltas to the destination rest orientation is appropriate. Select the export skeleton rather than the full control rig when the bridge should drive a separate deformation skeleton.

Object scale and unit conversion are separate: the global mapping code accounts for the armature object's world matrix, while the translation scale converts incoming position units. A non-unit uniform object scale does not automatically require applying transforms or entering that number as the translation scale.

## Character profiles

Open **N > CEB > Perfil de personagem** (character profile). Available suggestions include:

- Original exported skeleton (**Mesmo esqueleto exportado**).
- Rigify / Metarig.
- Generated Rigify deformation bones (**DEF**).
- Auto-Rig Pro deformation bones.
- Custom mapping (**Personalizado**).

Receive a pose to discover source joint names, stop the connection, select the destination and profile, and click **Gerar sugestoes de mapeamento** (generate mapping suggestions). Review every source-to-destination pair. Generated-rig presets are starting points, not certification of every rig version or configuration.

Add or remove pairs, disable joints, and select destination bones. **Somente pares habilitados** restricts changes to enabled pairs. Empty, duplicate, or missing enabled mappings prevent connection. Editing a mapping stops Live Link until the configuration is reviewed and reconnected.

Use **Salvar JSON / Importar JSON** to export or import the profile. Pairs are also stored in the `.blend` scene. Reverse key transfer uses the source names from the mapping.

The original-export profile selects **Global Transforms**; prepare connected joints before starting. Other suggestions select **Global Delta**. Check scale and coordinate conversion, and use **Root Only Translation** to preserve destination bone lengths when retargeting different proportions.

Profiles do not automatically solve IK controls, drivers, conflicting constraints, twist systems, or arbitrary rest-pose differences. Use an appropriate FK/deformation chain or a separate bake/retarget workflow for such rigs.

## Performance

Global pose application computes local matrices in a batch and evaluates the result once per pose, using Blender's bone inheritance conversion API. Identical preview retransmissions are skipped; frame changes and key revisions still get processed. **Network Configuration > Timer Poll (Hz)** controls the update interval.

In the Maya scene's background benchmark, pose application dropped from approximately **500 ms to 43 ms**. This measures that processing path, not guaranteed viewport FPS with full geometry, materials, and constraints.

## Limits and multiple characters

- The bridge can be configured for different characters sequentially. Multiple armatures may exist in Blender, but only **one target armature and one source connection are active in the current workflow**.
- Use one intended character skeleton per Cascadeur source scene. Independent simultaneous character links are not implemented; joints are keyed by name without separate character identifiers.
- Generic retargeting of Rigify/ARP control systems, complex constraints, NLA, and animated scale has not been validated. The detailed real-character validation was performed on Maya; other profile mappings have synthetic tests.
- Poses and keyframe numbers are transferred. Curve tangents and interpolation are not identical between applications. Bake when the complete sampled trajectory must be preserved.
- Background key synchronization can take a few seconds. Preview does not add a key at every received frame.
- Local transport uses UDP `127.0.0.1:9999` for Blender reception and `127.0.0.1:9998` for Cascadeur commands.
- An outgoing pose is limited to 65,000 bytes and produces an explicit error if exceeded. Large incoming snapshots are compressed and fragmented.
- After opening another `.blend` or switching characters, verify the target/profile and reconnect.

## Development

Blender sources: `cascadeur_live_link/`. Cascadeur sources: `cascadeur_scripts/`.

Build the addon ZIP:

```powershell
python tools/package_addon.py
```

Run isolated Blender regression tests without a Cascadeur session:

```powershell
blender --background --factory-startup --python tests/test_sparse_sync.py
blender --background --factory-startup --python tests/test_delete_regression.py
blender --background --factory-startup --python tests/test_exported_rig.py
blender --background --factory-startup --python tests/test_profiles.py
```

These cover sparse key synchronization, key deletion regressions, absolute joint transforms, local round trips, destination proportions, explicit profiles, JSON serialization, source names on return, and duplicate preview handling.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for upstream attribution.
