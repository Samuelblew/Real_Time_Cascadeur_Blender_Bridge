# Como usar a conexão Blender / Cascadeur

## Erro ao apagar frames — corrigido na v1.2.1

O log mostrou uma tentativa de gravar no frame 95 quando os dados da animação
do Cascadeur terminavam no frame 0. A timeline visual pode continuar mostrando
um intervalo maior, mas isso não significa que o armazenamento dos joints tem
esse mesmo tamanho. A Bridge escrevia os valores antes de preparar esse tamanho.

Foram corrigidos quatro pontos:

- Estender o armazenamento das tracks antes de gravar uma pose em um frame posterior.
- Remover chaves consecutivas do Blender de trás para frente, sem reutilizar referências
  de pontos que mudaram de posição após a remoção.
- Distinguir apagar canais/chaves de inserir uma chave ou mudar seu valor. Uma exclusão
  isolada no Blender não deve disparar o envio de uma pose como se fosse uma edição.
- Interromper a repetição automática da mesma operação rejeitada, mantendo a causa
  específica do erro visível. Uma nova edição, envio manual ou Retomar preview permite
  uma nova tentativa.

Reprodução verificada: cena separada com um único frame → envio no frame 95,
58 ossos aplicados e chave confirmada em 13 tracks. Exclusão de 123 chaves seguidas
no Blender deixa somente a chave 0. Ver collapsed_scene_results.json e
delete_regression_results.json.

Sua cena Blender antes da correção foi guardada em Before_Frame_Delete_Fix.blend.
Essa cópia permite recuperar a Action anterior se alguma exclusão foi acidental.
A correção não restaura automaticamente as chaves que você decidiu apagar.

## O que passa de um programa para o outro

O personagem/mesh precisa existir nos dois programas. Esta conexão transmite
transformações dos ossos e poses nos frames com chave; não transmite materiais,
texturas, geometria, rig completo nem ferramentas de AutoPosing.

Cascadeur → Blender: um timer lê a pose final dos joints e envia posições e rotações
por uma conexão local. O Blender converte os eixos e a escala e aplica a pose ao rig
escolhido. Isso atualiza o modelo enquanto você manipula no Cascadeur.

Em paralelo, o Cascadeur lista as chaves de suas tracks e envia as poses nesses
frames. O Blender as escreve na Action Cascadeur Sync, nos grupos dos ossos.
Essas chaves são sincronizadas em segundo plano, com uma pequena espera que depende
do tamanho da animação; a pose ao vivo não precisa esperar a lista inteira.

Blender → Cascadeur: a Bridge observa chaves inseridas ou valores alterados na
Action/slot ativos. Captura a pose nesses frames, envia e aguarda confirmação.
Arrastar um osso sem gravar chave não fica sendo transmitido para o Cascadeur.

Ao editar no Blender, a prévia pausa para não sobrescrever sua pose local antes de
você inserir uma chave. Não é necessário manter uma sincronização de arrasto nos
dois sentidos. Use Retomar preview se não quiser gravar a edição local.

## O que cada controle faz — N > CEB

| Controle | Função |
|---|---|
| Target Character Rig | Armature do Blender que recebe e envia poses. Trocar essa seleção pausa a conexão; confira o outro personagem e reconecte. |
| Start Live Link | Inicia recepção ao vivo e solicita o início do streamer no Cascadeur. O receptor Cascadeur deve estar iniciado. |
| Stop Live Link | Para o acompanhamento e a observação automática de chaves no Blender. |
| Sincronizar chaves Cascadeur → Blender | Espelha inserção, alteração, movimento e exclusão das chaves do Cascadeur na Action sincronizada. |
| Enviar ao inserir/alterar chaves no Blender | Envia poses automaticamente depois de inserir uma chave ou alterar seu valor na Action ativa. |
| Retomar preview do Cascadeur | Libera a pausa da edição local. A pose do Cascadeur passa a aparecer novamente; edições não gravadas podem ser substituídas pela prévia. |
| Enviar frame ao Cascadeur | Move o cursor de tempo do Cascadeur para o número atual no Blender. Não envia uma pose. |
| Buscar pose e gravar keyframe | Importação explícita de uma pose no frame atual do Blender. Requer parar Live Link. |
| Enviar pose ao mesmo frame | Envia a pose atual ao número do frame do Blender e aguarda confirmação. No modo Same Rig funciona também com Live Link ligado. |
| Inicio / Fim + Enviar keyframes do intervalo | Envia em lote os frames com chaves da Action ativa dentro desse intervalo. Não grava cada frame intermediário só por estar dentro do intervalo. |

A primeira conexão cria uma cópia da Action ativa para preservar a original. Cada
reconexão pode criar outra cópia, como Cascadeur Sync.001. A Action em uso aparece
no Action Editor. Os grupos dos ossos organizam as curvas; o agrupamento visual
das tracks do Cascadeur não é importado como uma estrutura de rig ou de pastas.

## Onde apagar as chaves

- Para a exclusão aparecer nos dois programas, apague no **Cascadeur**, com
  Sincronizar chaves Cascadeur → Blender ligado.
- Apagar no Blender não apaga automaticamente no Cascadeur nesta versão. Isso
  inclui mover uma chave no Blender: a pose é enviada ao novo frame, mas a chave
  antiga do Cascadeur não é removida automaticamente.
- Chaves que ainda existem no Cascadeur podem reaparecer no Blender quando a
  sincronização receber uma alteração da origem. Para editar uma Action só no
  Blender, pare Live Link ou desligue o espelhamento das chaves da origem.

Apagar chaves remove poses gravadas. Remover um trecho de tempo, deslocando tudo
o que vem depois, é uma operação diferente; a conexão não executa esse tipo de
montagem da timeline no outro programa.

## Adicionar um personagem novo

1. Pare Live Link antes de trocar de personagem.
2. No Blender, prepare o personagem com um esqueleto de deformação e guarde uma
   cópia. Use a Bridge/exportação que você já usa para levar esse mesmo esqueleto
   ao Cascadeur; esta extensão ao vivo não envia o modelo inicialmente.
3. No Cascadeur, importe em uma cena própria, configure o rig/AutoPosing como de
   costume e confira se mover os controles realmente move os joints e a malha.
4. Mantenha nomes dos ossos, hierarquia e referência de repouso consistentes.
   O teste validado usa o mesmo esqueleto de exportação nos dois programas.
5. Use o mesmo FPS nas duas cenas. A Bridge sincroniza números de frames; ela
   não converte automaticamente uma cena de 24 FPS para outra de 30 FPS.
6. No Cascadeur, inicie Scripts > Blender Live Link > Start Streamer.
7. No Blender, N > CEB, escolha o novo Armature em Target Character Rig.
8. Para o fluxo validado, use Pose Mode = Same Rig / Global Delta. Para o padrão
   Cascadeur em centímetros, use Y-Up to Z-Up e Translation Scale = 0.01. Outros
   pipelines podem exigir outra escala; teste uma pequena translação primeiro.
9. Confirme as opções de chaves e clique Start Live Link. Teste uma mão e um pé,
   depois uma chave em um frame conhecido, antes de sincronizar a animação inteira.

Para o primeiro teste, você pode deixar as duas opções de chaves desligadas e
verificar somente a pose ao vivo. Depois ligue o espelhamento das chaves para
criar a Action sincronizada.

Use um personagem por cena do Cascadeur nesta versão. Não há um seletor separado
para filtrar vários personagens dentro da mesma cena; nomes de joints repetidos
não são um caso suportado. A integração conecta um Armature por vez no Blender.
Trocar a cena do Cascadeur também exige reconexão para confirmar o destino.

Um rig Rigify/Auto-Rig Pro com controles e constraints não é equivalente a um
esqueleto de deformação limpo. Comece conectando o rig que foi exportado; transferir
o resultado para os controles do rig original exige retarget/adaptação específica.
A extensão ainda não automatiza essa etapa para qualquer personagem.

## Limites práticos

As poses e os tempos das chaves são transferidos; as tangentes/interpolação não
são idênticas entre programas. Use bake para reproduzir toda a trajetória.
Escala animada, NLA e rigs com constraints complexas não foram validados.
O envio de uma pose ao Cascadeur tem limite de 65000 bytes; rigs maiores geram
erro explícito. A conexão é local: os dois programas precisam estar na mesma máquina.
Ao abrir outro .blend, pressione Start Live Link novamente depois de conferir o rig.
