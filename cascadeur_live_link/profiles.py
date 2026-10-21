"""Editable, serializable source-joint to FK/deformation-bone profiles."""
import json
from pathlib import Path
import bpy
from bpy.props import StringProperty, BoolProperty, EnumProperty

PROFILES=[('EXPORTED','Mesmo esqueleto exportado','Nomes e orientacoes originais'),('METARIG','Rigify / Metarig','Ossos FK do metarig'),('RIGIFY','Rigify gerado (DEF)','Ossos de deformacao; controles IK ficam fora'),('ARP','Auto-Rig Pro (deformacao)','Sugestoes para ossos de deformacao ARP'),('CUSTOM','Personalizado','Mapeamento manual')]

def edited(self,context):
    from . import operators
    operators._bone_match_cache.clear()
    if context and getattr(context.scene,'cascadeur_live_link',None):
        s=context.scene.cascadeur_live_link
        if s.is_connected:
            bpy.ops.cascadeur.live_link_stop()

def key(name):
    return name.split(':')[-1].removeprefix('DEF-').lower()

def candidates(source,profile):
    n=key(source)
    aliases={'hips':'spine','pelvis':'spine','spine1':'spine.001','spine2':'spine.002','neck':'spine.004','head':'spine.006'}
    for side,long in [('l','left'),('r','right')]:
        aliases.update({long+'upleg':'thigh.'+side,long+'leg':'shin.'+side,long+'foot':'foot.'+side,long+'toebase':'toe.'+side,long+'shoulder':'shoulder.'+side,long+'arm':'upper_arm.'+side,long+'forearm':'forearm.'+side,long+'hand':'hand.'+side})
    n=aliases.get(n,n)
    if profile=='ARP':
        arp={'spine':'root.x','spine.001':'spine_01.x','spine.002':'spine_02.x','spine.003':'spine_03.x','spine.004':'neck.x','spine.005':'head.x','spine.006':'head.x'}
        for side in ['l','r']:
            for src,dst in [('thigh','thigh_stretch'),('shin','leg_stretch'),('foot','foot'),('toe','toes_01'),('shoulder','shoulder'),('upper_arm','arm_stretch'),('forearm','forearm_stretch'),('hand','hand')]:
                arp[src+'.'+side]=dst+'.'+side
        return [arp.get(n,n)]
    return [('def-'+n) if profile=='RIGIFY' else n]

class BonePair(bpy.types.PropertyGroup):
    source: StringProperty(name='Cascadeur',update=edited)
    target: StringProperty(name='Blender',update=edited)
    enabled: BoolProperty(name='Usar',default=True,update=edited)

class BuildProfile(bpy.types.Operator):
    bl_idname='cascadeur.build_profile'
    bl_label='Gerar sugestoes de mapeamento'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        from . import operators
        s=context.scene.cascadeur_live_link;arm=s.target_armature
        if not arm:
            self.report({'ERROR'},'Escolha a armature de destino');return {'CANCELLED'}
        if s.is_connected:
            bpy.ops.cascadeur.live_link_stop()
        packet=operators._last_packet_received or {}
        names=list(packet.get('bones',{}))
        if not names:
            self.report({'ERROR'},'Receba uma pose primeiro, ou adicione os pares manualmente');return {'CANCELLED'}
        s.bone_mapping.clear();used=set();found=0
        available={n.lower():n for n in arm.pose.bones.keys()}
        for source in names:
            row=s.bone_mapping.add();row.source=source
            opts=[source.lower()] if s.rig_profile=='EXPORTED' else candidates(source,s.rig_profile)
            target=next((available[n] for n in opts if n in available and available[n] not in used),'')
            row.target=target;row.enabled=bool(target)
            if target:used.add(target);found+=1
        s.mapping_only=True;s.auto_map_synonyms=False
        s.pose_mapping_mode='GLOBAL_MATRIX' if s.rig_profile=='EXPORTED' else 'GLOBAL_DELTA'
        self.report({'INFO'},f'{found}/{len(names)} pares sugeridos; revise antes de conectar')
        return {'FINISHED'}

class AddPair(bpy.types.Operator):
    bl_idname='cascadeur.add_bone_pair';bl_label='Adicionar par'
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        context.scene.cascadeur_live_link.bone_mapping.add();return {'FINISHED'}

class RemovePair(bpy.types.Operator):
    bl_idname='cascadeur.remove_bone_pair';bl_label='Remover par'
    index: bpy.props.IntProperty()
    bl_options={'REGISTER','UNDO'}
    def execute(self,context):
        s=context.scene.cascadeur_live_link
        if 0<=self.index<len(s.bone_mapping):s.bone_mapping.remove(self.index)
        edited(self,context);return {'FINISHED'}

class ProfileFile(bpy.types.Operator):
    bl_idname='cascadeur.profile_file';bl_label='Perfil JSON'
    filepath: StringProperty(subtype='FILE_PATH')
    action: EnumProperty(items=[('IMPORT','Importar',''),('EXPORT','Exportar','')])
    def invoke(self,context,event):
        if not self.filepath:self.filepath='cascadeur_character_profile.json'
        context.window_manager.fileselect_add(self);return {'RUNNING_MODAL'}
    def execute(self,context):
        s=context.scene.cascadeur_live_link
        try:
            if self.action=='EXPORT':
                data={'schema':1,'profile':s.rig_profile,'mode':s.pose_mapping_mode,'scale':s.translation_scale,'conversion':s.coord_conversion,'mapping_only':s.mapping_only,'root_only':s.apply_root_only_translation,'bones':[{'source':r.source,'target':r.target,'enabled':r.enabled} for r in s.bone_mapping]}
                Path(self.filepath).write_text(json.dumps(data,indent=2),encoding='utf-8')
            else:
                data=json.loads(Path(self.filepath).read_text(encoding='utf-8'))
                assert data['schema']==1
                assert data['mode'] in {x.identifier for x in s.bl_rna.properties['pose_mapping_mode'].enum_items}
                assert float(data['scale'])>0
                assert data['conversion'] in {x.identifier for x in s.bl_rna.properties['coord_conversion'].enum_items}
                rows=data['bones'];assert isinstance(rows,list) and len(rows)<=2000
                assert all(isinstance(r['source'],str) and isinstance(r['target'],str) and isinstance(r.get('enabled',True),bool) for r in rows)
                assert len({r['source'] for r in rows})==len(rows)
                assert len({r['target'] for r in rows if r.get('enabled',True)})==sum(r.get('enabled',True) for r in rows)
                if s.is_connected:bpy.ops.cascadeur.live_link_stop()
                s.bone_mapping.clear()
                for r in rows:
                    row=s.bone_mapping.add();row.source=r['source'];row.target=r['target'];row.enabled=r.get('enabled',True)
                s.pose_mapping_mode=data['mode'];s.translation_scale=float(data['scale']);s.coord_conversion=data['conversion']
                s.mapping_only=data.get('mapping_only',True);s.apply_root_only_translation=data.get('root_only',True)
                s.rig_profile='CUSTOM'
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}
        return {'FINISHED'}

classes=(BuildProfile,AddPair,RemovePair,ProfileFile)
def register():
    for cls in classes:bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(classes):bpy.utils.unregister_class(cls)
