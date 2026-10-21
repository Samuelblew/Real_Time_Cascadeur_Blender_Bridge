# Real Time Cascadeur Blender Bridge

Integração local de poses e keyframes entre **Blender 5.2** e **Cascadeur 2026.2**, baseada no [Blender_Cascadeur_RT de Carlos Barreto / Antigravity](https://github.com/carlosedubarreto/Blender_Cascadeur_RT).

Versão atual: **1.2.1**, protótipo validado com o mesmo esqueleto de deformação nos dois programas.

## Recursos

- **Cascadeur → Blender:** prévia contínua da pose, inclusive ao mudar a pose sem trocar de frame.
- **Chaves Cascadeur → Blender:** espelhamento de inserção, alteração, movimento e exclusão na Action sincronizada, nos grupos dos ossos.
- **Blender → Cascadeur:** envio automático ao inserir uma chave ou alterar seu valor; envio manual de uma pose ou de vários frames com chaves.
- Action original preservada por cópia; proteção contra reenvios em ciclo e troca acidental de cena/personagem.
- Correção de envio para frames posteriores quando a animação do Cascadeur foi encurtada após apagar chaves.

## Instalação — são dois componentes

**O addon do Blender NÃO instala os scripts do Cascadeur automaticamente.** Instale os dois lados uma vez.

### 1. Blender

1. Baixe [Cascadeur_Live_Sync_1_2_1.zip](dist/Cascadeur_Live_Sync_1_2_1.zip).
2. No Blender 5.2: **Edit → Preferences → Add-ons → Install from Disk**.
3. Escolha o ZIP e habilite **Cascadeur Real-Time Live Link**.
4. O painel fica na viewport, **N → CEB**.

### 2. Cascadeur

Baixe ou clone o repositório inteiro, mantendo `cascadeur_scripts/` ao lado do instalador. Feche o Cascadeur antes de instalar.

No PowerShell, na pasta do repositório:

```powershell
.\install_cascadeur_menu.ps1 -CascadeurPath 'C:\Program Files\Cascadeur'
```

Substitua o caminho pela pasta que contém `cascadeur.exe`. Se essa pasta exigir permissão de administrador, execute o PowerShell como administrador. O instalador não altera a política de execução do Windows, não baixa dependências e guarda uma cópia da pasta anterior quando ela já existe.

Ele copia os scripts para:

```text
CASCADEUR/resources/scripts/python/scripts/blender_live_link/
```

**Alternativa manual:** crie essa pasta, copie para ela os arquivos `.py` de `cascadeur_scripts/cascadeur_live_link_package/` e também `cascadeur_scripts/cascadeur_streamer.py` e `cascadeur_scripts/live_sync.py`.

Reabra o Cascadeur. Deve aparecer **Scripts → Blender Live Link → Start Streamer**. Não é necessário instalar o REA, o MCP ou pacotes Python externos para usar a Bridge. Ela usa a API `csc` e o Qt disponíveis no Cascadeur.

## Primeira conexão

1. Abra o mesmo personagem/esqueleto nos dois programas. O modelo precisa ser importado antes, pela sua Bridge/exportação FBX habitual.
2. No Cascadeur, execute **Scripts → Blender Live Link → Start Streamer**.
3. No Blender, **N → CEB**, escolha **Target Character Rig**.
4. Para o fluxo validado: **Same Rig / Global Delta**, **Y-Up to Z-Up**, escala **0.01** para dados em centímetros. Use o mesmo FPS nas duas cenas.
5. Marque as opções de sincronização desejadas e clique **Start Live Link**. A pose passa a acompanhar ao vivo.

Ao editar a pose no Blender, a prévia pausa para não sobrescrever seu trabalho. Insira uma chave para enviar essa pose; ou clique **Retomar preview do Cascadeur** se não quiser gravar a edição.

**Para apagar chaves nos dois programas, apague no Cascadeur.** Exclusões no Blender não apagam chaves do Cascadeur nesta versão; mover uma chave no Blender envia a pose ao novo frame, mas não remove a chave antiga da origem.

Veja o [guia de controles e novos personagens](docs/GUIA_USO_E_PERSONAGENS.md) e os [testes realizados](docs/VALIDATION.md).

## Limites

- Um Armature conectado por vez no Blender e um personagem por cena do Cascadeur.
- Mesmos nomes, hierarquia e referência de exportação. Retarget genérico de controles Rigify/ARP, constraints complexas, NLA e escala animada não foram validados.
- As poses e os frames das chaves são transferidos; tangentes/interpolação não são idênticas entre programas. Para preservar toda a trajetória, use bake.
- A sincronização das chaves ocorre em segundo plano e pode levar alguns segundos. A prévia não cria chaves extras.
- Conexão local: UDP `127.0.0.1:9999` para receber no Blender e `127.0.0.1:9998` para comandos no Cascadeur.
- O envio de uma pose ao Cascadeur tem limite de 65000 bytes, com erro explícito se excedido. Snapshots grandes no sentido inverso são comprimidos e fragmentados.
- Ao abrir outro `.blend` ou trocar de personagem, confira o destino e reconecte.

## Desenvolvimento

Código Blender: `cascadeur_live_link/`. Código Cascadeur: `cascadeur_scripts/`. Gerar o ZIP:

```powershell
python tools/package_addon.py
```

Testes isolados no Blender, sem depender de uma sessão do Cascadeur:

```powershell
blender --background --factory-startup --python tests/test_sparse_sync.py
blender --background --factory-startup --python tests/test_delete_regression.py
```

Consulte [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) para a origem do código.
