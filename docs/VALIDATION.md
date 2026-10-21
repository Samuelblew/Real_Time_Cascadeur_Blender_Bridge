# Validação da v1.2.1

Ambiente testado: Windows, Blender 5.2.1 LTS e Cascadeur 2026.2. Personagem usado:
exemplo oficial Backflip, com o mesmo esqueleto exportado e importado no Blender.
Os modelos e arquivos pessoais de teste não são distribuídos neste repositório.

Testes de integração executados durante o desenvolvimento:

- 58 ossos, poses nos frames 10, 30 e 73 comparadas à referência FBX; erro máximo
  de posição inferior a 0.04 mm nos frames testados e rotação inferior a 0.11 grau.
- Ida e volta da pose para o frame 40 do Cascadeur, com confirmação em 13 tracks.
- Prévia com 16 poses distintas no mesmo frame 12, sem chaves extras no Blender.
- Aproximadamente 31 atualizações por segundo na sessão de exemplo; não é garantia
  de desempenho em outros rigs ou máquinas.
- Uma alteração de chave no Blender enviada ao frame 42 sem reenvios em ciclo;
  lote dos frames 10 e 30 confirmado.
- Animação reduzida a uma chave no frame 0: envio no frame 95 passou após preparar
  o armazenamento da animação, com 58 ossos e chaves confirmadas em 13 tracks.
- Abrir outro `.blend` interrompe a conexão e impede envios automáticos acidentais.

Testes reproduzíveis incluídos:

- `test_sparse_sync.py`: criação, movimento e exclusão a partir de snapshots esparsos.
- `test_delete_regression.py`: redução de 124 chaves a uma, exclusões sem envio de
  pose, alteração de valor detectada e uma única tentativa para uma edição rejeitada.

Os testes isolados verificam o consumidor Blender. As verificações de integração
acima exigiram Cascadeur em execução e foram feitas em cenas separadas.
