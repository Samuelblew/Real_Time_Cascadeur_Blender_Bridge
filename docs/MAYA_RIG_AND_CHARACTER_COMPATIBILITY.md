# Correção do rig da Maya e compatibilidade de personagens — 1.4.0

## O que foi diagnosticado

A cena tinha quatro armatures com responsabilidades diferentes: Maya Casc Rig (82 ossos), Rig (136), Eye's rig (5) e Slider Bones (4). A conexão deveria escrever apenas no esqueleto de exportação Maya Casc Rig. Os nomes dos 82 joints coincidiam com a origem; a falha principal não era ausência de ossos ou necessidade de reconstruir o personagem.

Havia duas incompatibilidades de transformação. A aplicação de deltas sobre a orientação de repouso do Blender acrescentava uma rotação de aproximadamente 90 graus nesse export específico. Mesmo corrigindo a orientação absoluta, 53 ossos com Connected ativo continuavam impedindo reproduzir as translações dos joints. O maior desvio de posição residual nesse teste era aproximadamente 6,1 cm.

## Orientações globais e espaço do objeto

Global Delta combina a variação de orientação da origem com a orientação de repouso do destino. É útil em outra classe de mapeamento, mas os joints desta exportação já carregavam a orientação global necessária. O modo Global Transforms foi criado para usar diretamente essa informação, convertendo o sistema de coordenadas uma vez.

Com C representando a conversão de Y-up para Z-up, a orientação desejada nesse modo é C × q_global_origem. A posição é C × (posição_global_origem × escala_de_unidades). No caso testado, a escala de unidades era 0,01; o objeto da armature também tinha escala uniforme de aproximadamente 1,387166. A matriz mundial do objeto entra na transformação e é invertida para obter a pose no espaço da armature. Aplicar escala do objeto novamente, ignorá-la ou acrescentar a rest pose outra vez produz deslocamentos/orientações incorretos.

Isso não significa que basta somar 90 graus em qualquer personagem. A direção da conversão, o espaço de cada matriz e a origem da orientação importada precisam ser consistentes.

## Parenting e Connected

Parent define a hierarquia. Connected acrescenta a restrição de que a cabeça de um osso filho deve ficar na cauda do pai. Continuar parentado não exige Connected.

A preparação desmarcou Connected nos 53 ossos do esqueleto de exportação. Preservou os pais, as matrizes de repouso, pesos e nomes. As matrizes de pose foram restauradas após a alteração, mantendo seleção e modo do usuário. Se a armature compartilhar seus dados com outro objeto, a preparação copia esses dados primeiro.

Não foi feita uma limpeza geral das constraints, controles, cabelos ou rig facial. As outras três armatures tiveram a estrutura preservada. Elas podem se mover por seus vínculos existentes com Maya Casc Rig, mesmo sem receber escrita direta da ponte.

## Por que a primeira atualização não corrigiu a cena original

O addon 1.3 estava instalado, mas a cena original continuava em Global Delta e conservava os 53 ossos conectados. A preparação anterior havia ficado em uma cópia da cena. Instalar código não substitui automaticamente as propriedades e os dados de todas as cenas existentes.

A correção seguinte recarregou o addon na sessão ativa, selecionou Global Transforms, preparou a armature certa e salvou as propriedades na cena original, depois de criar backup. O painel exibe a versão do addon para tornar essa verificação visível.

## Desempenho

O caminho anterior atualizava o dependency graph entre níveis da hierarquia. Em uma cena com geometria e outras armatures dependentes, isso reavaliava muito mais que o único joint recém-alterado.

A aplicação foi reescrita para calcular os destinos na ordem dos pais, converter cada destino em matrix_basis com Bone.convert_local_to_pose e avaliar o resultado em lote. Os filhos usam as matrizes desejadas dos pais durante o cálculo, sem depender de uma avaliação completa intermediária da cena. A implementação respeita as opções de herança oferecidas pela API do Blender.

Também foram removidas avaliações de frame quando o cursor já está no frame correto. Retransmissões idênticas de preview são ignoradas; uma revisão de chaves invalida esse cache. O intervalo de polling considera o tempo já gasto processando o callback.

No benchmark em background da cena testada, quatro amostras antes ficaram entre 497,6 e 508,6 ms por pose; depois, entre 42,6 e 44,9 ms. É uma redução de aproximadamente 11,5 vezes nesse trecho. Não é uma garantia de FPS da viewport, que também depende de geometria, materiais, constraints, interface e frequência de alterações reais.

## Retorno Blender → Cascadeur e chaves

No modo de esqueleto exportado, o retorno inclui a orientação global absoluta e as transformações locais em relação ao pai exportado. Isso evita reconstruir esses valores usando hipóteses inadequadas sobre a transformação do pai do root. O receptor reconhece esses campos. Os pares personalizados são invertidos ao enviar, de modo que o destino receba seus nomes de origem.

Preview continua separado de gravação: receber uma pose não cria uma chave em todo frame. As chaves esparsas são sincronizadas na Action da ponte. A atualização inicial cria uma cópia da Action ativa antes de editar os canais sincronizados; original e cópia continuam disponíveis. Os grupos de ossos e os números dos frames são preservados pelo mecanismo de sincronização.

## O que foi validado

Na sessão ao vivo da Maya, no frame 89, os 82 ossos apresentaram erro máximo de posição de aproximadamente 0,0043 mm e erro máximo de orientação de aproximadamente 0,000010 grau em relação ao snapshot recebido. A Action sincronizada tinha chaves nos frames 0, 26, 68, 73, 83, 104 e 139.

São medidas das transformações dos ossos no snapshot avaliado. Não certificam todas as poses possíveis, controles de rosto/cabelo, todos os tipos de constraints ou uma frequência constante de renderização. O round trip de transformações locais também foi verificado em teste; isso não equivale a certificar todo fluxo bidirecional em qualquer rig.

Os testes do repositório cobrem transforms absolutos, preparação dos joints, proporções do destino em Global Delta com translação somente do root, pares de perfil, JSON, nomes de retorno, descarte de preview repetido e regressões de criação/movimento/exclusão de chaves.

## Mais de um personagem

A ponte pode ser reutilizada para personagens diferentes, selecionando outra armature e seu perfil. Os pares e opções ficam na cena Blender e podem ser exportados/importados em JSON. É possível manter várias armatures na cena, mas existe um único Target Armature por cena e uma conexão/origem ativa no fluxo atual.

Portanto, compatibilidade com personagens diferentes não significa sincronização simultânea de dois personagens independentes. O protocolo atual organiza joints por nome, sem um identificador independente de personagem; nomes repetidos na mesma cena de origem podem colidir. Portas diferentes, sozinhas, não acrescentam isolamento completo ao estado atual dos módulos.

Para uso sequencial: pare o link, selecione o personagem de origem apropriado (preferencialmente uma cena com somente o esqueleto desejado), selecione o destino, carregue/revise seu perfil e reconecte. Isso é importante também para impedir o reaproveitamento de chaves da origem anterior.

Há sugestões iniciais para Metarig, Rigify DEF e deformação Auto-Rig Pro. Elas ajudam a mapear nomes, mas ainda exigem validação com personagens reais desses sistemas. Não incluem uma solução universal de IK, drivers, twist, rest poses arbitrárias ou constraints que sobreponham o resultado. A validação detalhada desta correção foi feita na Maya; os demais perfis têm testes sintéticos de mapeamento.

Para sincronização simultânea, a evolução necessária é identificar personagens no protocolo e manter conexão, mapeamento, Action, caches, revisões e validação de origem separados por par de personagens.
