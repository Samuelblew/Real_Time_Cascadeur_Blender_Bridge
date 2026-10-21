# Real Time Cascadeur Blender Bridge

IntegraÃ§Ã£o local de poses e keyframes entre **Blender 5.2** e **Cascadeur 2026.2**, baseada no [Blender_Cascadeur_RT de Carlos Barreto / Antigravity](https://github.com/carlosedubarreto/Blender_Cascadeur_RT).

VersÃ£o atual: **1.4.0**, protÃ³tipo validado com o mesmo esqueleto de deformaÃ§Ã£o nos dois programas.

## Recursos

- **Cascadeur â†’ Blender:** prÃ©via contÃ­nua da pose, inclusive ao mudar a pose sem trocar de frame.
- **Chaves Cascadeur â†’ Blender:** espelhamento de inserÃ§Ã£o, alteraÃ§Ã£o, movimento e exclusÃ£o na Action sincronizada, nos grupos dos ossos.
- **Blender â†’ Cascadeur:** envio automÃ¡tico ao inserir uma chave ou alterar seu valor; envio manual de uma pose ou de vÃ¡rios frames com chaves.
- Action original preservada por cÃ³pia; proteÃ§Ã£o contra reenvios em ciclo e troca acidental de cena/personagem.
- CorreÃ§Ã£o de envio para frames posteriores quando a animaÃ§Ã£o do Cascadeur foi encurtada apÃ³s apagar chaves.

## InstalaÃ§Ã£o â€” sÃ£o dois componentes

**O addon do Blender NÃƒO instala os scripts do Cascadeur automaticamente.** Instale os dois lados uma vez.

### 1. Blender

1. Baixe [Cascadeur_Live_Sync_1_4_0.zip](dist/Cascadeur_Live_Sync_1_4_0.zip).
2. No Blender 5.2: **Edit â†’ Preferences â†’ Add-ons â†’ Install from Disk**.
3. Escolha o ZIP e habilite **Cascadeur Real-Time Live Link**.
4. O painel fica na viewport, **N â†’ CEB**.

### 2. Cascadeur

Baixe ou clone o repositÃ³rio inteiro, mantendo `cascadeur_scripts/` ao lado do instalador. Feche o Cascadeur antes de instalar.

No PowerShell, na pasta do repositÃ³rio:

```powershell
.\install_cascadeur_menu.ps1 -CascadeurPath 'C:\Program Files\Cascadeur'
```

Substitua o caminho pela pasta que contÃ©m `cascadeur.exe`. Se essa pasta exigir permissÃ£o de administrador, execute o PowerShell como administrador. O instalador nÃ£o altera a polÃ­tica de execuÃ§Ã£o do Windows, nÃ£o baixa dependÃªncias e guarda uma cÃ³pia da pasta anterior quando ela jÃ¡ existe.

Ele copia os scripts para:

```text
CASCADEUR/resources/scripts/python/scripts/blender_live_link/
```

**Alternativa manual:** crie essa pasta, copie para ela os arquivos `.py` de `cascadeur_scripts/cascadeur_live_link_package/` e tambÃ©m `cascadeur_scripts/cascadeur_streamer.py` e `cascadeur_scripts/live_sync.py`.

Reabra o Cascadeur. Deve aparecer **Scripts â†’ Blender Live Link â†’ Start Streamer**. NÃ£o Ã© necessÃ¡rio instalar o REA, o MCP ou pacotes Python externos para usar a Bridge. Ela usa a API `csc` e o Qt disponÃ­veis no Cascadeur.

## Primeira conexÃ£o

1. Abra o mesmo personagem/esqueleto nos dois programas. O modelo precisa ser importado antes, pela sua Bridge/exportaÃ§Ã£o FBX habitual.
2. No Cascadeur, execute **Scripts â†’ Blender Live Link â†’ Start Streamer**.
3. No Blender, **N â†’ CEB**, escolha **Target Character Rig**.
4. Para o fluxo validado: **Same Rig / Global Delta**, **Y-Up to Z-Up**, escala **0.01** para dados em centÃ­metros. Use o mesmo FPS nas duas cenas.
5. Marque as opÃ§Ãµes de sincronizaÃ§Ã£o desejadas e clique **Start Live Link**. A pose passa a acompanhar ao vivo.

Ao editar a pose no Blender, a prÃ©via pausa para nÃ£o sobrescrever seu trabalho. Insira uma chave para enviar essa pose; ou clique **Retomar preview do Cascadeur** se nÃ£o quiser gravar a ediÃ§Ã£o.

**Para apagar chaves nos dois programas, apague no Cascadeur.** ExclusÃµes no Blender nÃ£o apagam chaves do Cascadeur nesta versÃ£o; mover uma chave no Blender envia a pose ao novo frame, mas nÃ£o remove a chave antiga da origem.

Veja o [guia de controles e novos personagens](docs/GUIA_USO_E_PERSONAGENS.md) e os [testes realizados](docs/VALIDATION.md).

## Limites

- Um Armature conectado por vez no Blender e um personagem por cena do Cascadeur.
- Mesmos nomes, hierarquia e referÃªncia de exportaÃ§Ã£o. Retarget genÃ©rico de controles Rigify/ARP, constraints complexas, NLA e escala animada nÃ£o foram validados.
- As poses e os frames das chaves sÃ£o transferidos; tangentes/interpolaÃ§Ã£o nÃ£o sÃ£o idÃªnticas entre programas. Para preservar toda a trajetÃ³ria, use bake.
- A sincronizaÃ§Ã£o das chaves ocorre em segundo plano e pode levar alguns segundos. A prÃ©via nÃ£o cria chaves extras.
- ConexÃ£o local: UDP `127.0.0.1:9999` para receber no Blender e `127.0.0.1:9998` para comandos no Cascadeur.
- O envio de uma pose ao Cascadeur tem limite de 65000 bytes, com erro explÃ­cito se excedido. Snapshots grandes no sentido inverso sÃ£o comprimidos e fragmentados.
- Ao abrir outro `.blend` ou trocar de personagem, confira o destino e reconecte.

## Desenvolvimento

CÃ³digo Blender: `cascadeur_live_link/`. CÃ³digo Cascadeur: `cascadeur_scripts/`. Gerar o ZIP:

```powershell
python tools/package_addon.py
```

Testes isolados no Blender, sem depender de uma sessÃ£o do Cascadeur:

```powershell
blender --background --factory-startup --python tests/test_sparse_sync.py
blender --background --factory-startup --python tests/test_delete_regression.py
```

Consulte [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para a origem do cÃ³digo.


## Rig original exportado (exemplo: Maya Casc Rig)

Para o mesmo esqueleto exportado do Blender, selecione explicitamente a armature de deformação em **Target Armature**, e escolha **Same Exported Rig / Global Transforms**. Esse modo usa as orientações globais originais dos joints, sem aplicar novamente a orientação de repouso do Blender. Use nomes exatos e desative o mapeamento por sinônimos quando os nomes já coincidirem.

Com Live Link parado, clique **Permitir translacao dos joints** uma vez. Isso desmarca **Connected** somente nos ossos da armature escolhida: os pais, posições de repouso e pesos continuam preservados. A opção permite receber os deslocamentos dos joints do Cascadeur; o modo Connected do Blender força a cabeça do filho na cauda do pai e impede reproduzir certas poses. Salve uma cópia antes de preparar um personagem novo.

O modo anterior **Same Rig / Global Delta** continua disponível para rigs cuja orientação tenha sido reconstruída na importação. Não selecione automaticamente o rig de controles completo quando a ponte deve atuar em um esqueleto de exportação separado.


## Perfis de personagem e desempenho (1.4)

Em **N → CEB → Perfil de personagem**, escolha **Mesmo esqueleto exportado**, **Rigify / Metarig**, **Rigify gerado (DEF)**, **Auto-Rig Pro (deformação)** ou **Personalizado**. Receba uma pose para descobrir os nomes de origem, pare a conexão, escolha o destino e clique **Gerar sugestões de mapeamento**. Revise cada par Cascadeur → Blender; as sugestões de rigs gerados são pontos de partida, não certificação de qualquer versão ou configuração de rig.

É possível adicionar/remover pares, desabilitar joints e selecionar o osso de destino. **Somente pares habilitados** impede alterações em ossos fora do perfil. Pares ativos vazios, duplicados ou apontando para ossos ausentes impedem iniciar a conexão. Alterar os pares para a conexão até nova revisão. **Salvar JSON / Importar JSON** permite reutilizar o perfil em outros arquivos e personagens; os pares também são salvos no .blend. O envio de chaves de volta utiliza os nomes de origem do perfil.

Para o esqueleto original exportado, o perfil seleciona **Global Transforms**; use o botão de liberar translação dos joints uma vez antes de iniciar. Para rigs com orientação/proporções diferentes, a sugestão usa **Global Delta**; confira escala e conversão, e habilite **Root Only Translation** para conservar comprimentos do destino. Os perfis não resolvem controles IK, drivers, constraints conflitantes ou diferenças arbitrárias de rest pose automaticamente. Em rigs com IK, use uma cadeia FK/deformação apropriada ou bake/retarget antes de voltar aos controles de animação.

A aplicação global foi reescrita para converter as matrizes locais em lote e avaliar a cena uma vez por pose, respeitando os modos de herança dos ossos. Poses retransmitidas sem mudança são ignoradas; mudanças de frame e revisões das chaves continuam processadas. **Network Configuration → Timer Poll (Hz)** agora controla o intervalo de atualização. O teste com a cena da Maya caiu de cerca de 500 ms para 43 ms por aplicação em background; isso não garante o mesmo FPS na viewport com materiais, geometria e constraints completos.

Testes adicionais em Blender 5.2: `tests/test_exported_rig.py` valida transforms globais, translações livres, round trip e conservação de proporções; `tests/test_profiles.py` valida presets, pares explícitos, JSON, nomes de retorno e descarte de preview duplicado.


A explicação detalhada da correção e dos limites de múltiplos personagens está em [Rig da Maya e compatibilidade](docs/MAYA_RIG_AND_CHARACTER_COMPATIBILITY.md).
