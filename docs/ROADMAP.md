# Roadmap e decisões de arquitetura

## O que existe hoje (v0.2)

O add-on usa as bibliotecas Python puras do Ladybug Tools tal como são
(`ladybug-core`, `ladybug-geometry`, `ladybug-radiance`, `ladybug-comfort`,
`ladybug-display`), embutidas como wheels na Extension. Sun path, EPW, legendas,
cores, rosas de vento e radiação, domo de céu: tudo isso é o Ladybug original.

O que foi reescrito são as duas peças que no Ladybug dependem de binários do
Radiance:

| Radiance | Substituto nativo |
|---|---|
| `gendaymtx` (matriz de céu) | `core/skymatrix.py`: Perez all-weather em numpy, sol distribuído nos 3 patches mais próximos com os pesos do gendaymtx |
| `rcontrib` (visibilidade céu × sensor) | `core/intersect.py`: `mathutils.bvhtree` do Blender |

Isso é, na prática, um motor de radiação **sem interreflexão**: correto para
radiação incidente, horas de sol e fator de vista de céu; insuficiente para
iluminação natural com bounces. As legendas devem deixar isso claro.

## Fases

### Fase 1: consolidar e validar (curto prazo)

1. ~~**Matriz de visibilidade em cache**~~ (feito na v0.4). Cada cópia de
   resultado guarda em memória a matriz `sensores × patches` (radiação, que
   independe do período) ou `sensores × horas traçadas` (horas de sol, com a
   opção *Compute Full Year*). O painel *Period Explorer* recolore por ano,
   mês, dia ou faixa de horas sem novo ray tracing. Desde a v0.4.1 o cache é
   gravado no objeto de resultado e sobrevive ao fechamento do arquivo.
2. ~~**Metadados nos resultados.**~~ (feito na v0.3) Custom properties com
   EPW, período, timestep, densidade, offset, contexto, norte.
3. ~~**EPW Summary mais informativo.**~~ (feito na v0.4.1) Mostra fonte,
   estação, anos por mês e `COMMENTS 1/2`. Também na v0.4.1: presets de
   período (solstícios, equinócio, verão, inverno, ano, conforme o
   hemisfério), aviso de escala para FBX em centímetros e cache de
   visibilidade persistido no `.blend`.
4. ~~**Validação contra o Radiance.**~~ (feito) `tests/validate_radiance.py`
   compara com o `gendaymtx` 6.0 patch a patch: correlação ≥ 0,988, totais
   dentro de 0,7 %, superfícies desobstruídas dentro de 1,5 %; referência
   gravada em `tests/fixtures/` e verificada pelo teste normal. Achado de
   passagem: o parser do `ladybug_radiance.SkyMatrix` 0.2.x pula um número
   fixo de linhas de cabeçalho e, com a linha `LATLONG=` do Radiance 6.0,
   desloca os patches em um (vale reportar upstream). A interseção também
   foi validada (`tests/validate_rcontrib.py`): horas de sol idênticas ao
   `rcontrib` em 100 % dos raios; radiação anual com diferença média de
   0,02 % e máxima de 1,6 % do valor máximo.
5. **Gráficos 2D** (psicrométrica, hourly plot, barras mensais) como SVG
   importado em curvas ou PNG no Image Editor, em vez de geometria 3D.
6. Publicar: GitHub, OSArch, fórum do Ladybug Tools.

### Fase 2: ponte de modelo (o diferencial)

Montar um `honeybee.Model` (Room → Face → Aperture/Door, com construções e
programas) a partir do Blender e exportar **HBJSON**. O HBJSON é consumido por
Grasshopper, Pollination e pela CLI do Honeybee, então isso desacopla "montar
o modelo" (BIM, dentro do Blender) de "rodar o motor" (pode ser externo).

**Estado (v0.5, protótipo):** `core/ifc_bridge.py` lê o IFC com o ifcopenshell
do Bonsai e monta o modelo: `IfcSpace` → Room a partir do sólido da zona
(fechado, volume igual ao IFC); `IfcRelSpaceBoundary` classifica cada face
(elemento, interno/externo, solo); `IfcWindow/IfcDoor` → Aperture/Door;
`IfcMaterialLayerSet` + `Pset_MaterialThermal` → construções opacas; elementos
sem limite (cobertura, lajes, muros) → Shades de contexto. Paredes internas
são emparelhadas através da espessura (limites de 1º nível ficam na face
interna de cada lado). Painel *Honeybee (IFC)* desenha os rooms e exporta
HBJSON. Testado com a casa térrea do escritório: 12 rooms, 98 faces, 3 s.

Pendências da ponte:

- Faces internas emparelhadas com áreas diferentes (uma parede de um quarto
  encosta em dois vizinhos). Desde a v0.6.4 as faces **coincidentes** (laje
  sob vários ambientes) são divididas por `Room.intersect_adjacency`; falta
  o caso das paredes, cujas faces ficam a uma espessura de distância:
  projetar a face do vizinho e dividir (`Face3D.coplanar_split`) ou mover
  ambas para o eixo da parede.
- Zonas sem parede entre si viram *AirBoundary* só quando coincidem; garagem
  aberta continua como Outdoors.
- Aberturas sem face hospedeira (porta na esquina, boundary fora do plano) são
  descartadas com aviso.
- Programas de uso, ventilação e HVAC ainda não são atribuídos (defaults do
  Honeybee); vem com a fase 3.
- Exportar também para o Blender simples (sem IFC) por coleções e materiais.

### Fase 3: Honeybee-Energy

**Estado (v0.6, protótipo funcional):** `core/energy_sim.py` + `ops/energy.py`.
Com o EnergyPlus instalado (auto-detectado em `C:\EnergyPlus*`, ou caminho no
painel), o botão *Simulate* na caixa EnergyPlus do painel Honeybee:

1. Atribui programas residenciais pelo nome do ambiente (quarto, sala,
   cozinha, banheiro, serviço, garagem): pessoas, iluminação, equipamentos,
   infiltração e setpoints 18/26 °C, com horários diários simples.
2. *Free Running* (padrão): sem HVAC, janelas externas abrem quando o interior
   passa de 22 °C e o exterior está entre 16 e 32 °C (`ZoneVentilation:
   WindandStackOpenArea`). *Ideal Air*: aquecimento e resfriamento ideais nos
   ambientes ocupados.
3. Escreve o IDF (honeybee-energy + `Site:Location` e temperaturas do solo do
   EPW), roda o `energyplus.exe`, lê o SQLite (`ladybug.sql`).
4. Colore os rooms por métrica (horas acima/abaixo do conforto, % de horas
   confortáveis, temperatura operativa média/máx/mín) com legenda, e imprime
   o relatório por ambiente.

Casa térrea de teste: 12 zonas, ano inteiro em 9 min (558 s) com 776
superfícies de sombreamento; 7 dias em 104 s. Resultados coerentes: Sala com
pé-direito duplo e vidro é a mais quente e a mais fria; garagem fria.

v0.6.1: fração operável por tipo de janela (`JA01=0, JA02=0.5, JA04=0.75`),
espaço "ático/forro" como tipo de zona sem cargas e muito ventilada, e contexto
sem faces viradas para baixo nem abaixo do nível dos ambientes (776 → 479
superfícies; 7 dias em 66 s). O ático entre laje e telhado **não** é modelado
se não existir como zona no IFC: crie uma Zona "Ático" no ArchiCAD entre a
laje e o telhado para que a laje troque calor com o forro em vez de ficar
exposta ao exterior.

v0.6.2: pares internos recebem a mesma construção (ou invertida) nos dois
lados, exigência do EnergyPlus; os beirais e as partes de telhados/lajes/paredes
que sobram além dos rooms entram como sombra (diferença booleana coplanar);
o contexto é lido de volta da cena ao simular, então `HB Context` (e qualquer
malha com a propriedade `hb_shade`) pode ser editado à mão. Com os três
áticos do IFC: 15 zonas, 7 dias em 160 s.

v0.6.4: translator enxuto no ArchiCAD (ver `docs/IFC_ARCHICAD.md`: sem
quantidades, filtro *Elementos Construtivos com Zonas*, geometria por
extrusão) exporta em segundos em vez de travar. Com ele as paredes vêm como
CSG e com `IfcMaterialProfileSet` em vez de camadas: a ponte recupera o
`IfcMaterialLayerSet` de mesmo nome, e a mesclagem coplanar passa a cair numa
união booleana quando a junção por arestas falha (triangulações com junções
em T), agora também entre elementos e descartando o que fica dentro de uma
zona. Sombras: 1117 → 468. Faces coincidentes são divididas por ambiente
antes de emparelhar (`Room.intersect_adjacency`), e o emparelhamento passa a
considerar todas as faces não pareadas, não só as marcadas INTERNAL (o
ArchiCAD marca EXTERNAL o forro sob um ático): 30 pares internos em vez de 8,
sem avisos de área diferente. Os dois IFCs (com e sem quantidades) geram o
mesmo modelo. Portas de vidro (portas de correr/pivotantes externas, ou
listadas em *Glass Doors*) viram `Door(is_glass=True)` com construção de
vidro e abertura de ventilação; uma janela sobreposta a outra por poucos
centímetros é encolhida em vez de descartada, e uma porta que cruza duas
faces da parede é recortada em cada uma (JA04 e PA03 da Sala, antes
perdidas). Vigas e `IfcMember` entram no contexto (pergolado); para
sombreamento as paredes de contexto são sólidas (sem furos), porque faces
com furos viram polígonos não convexos que o EnergyPlus marca como *severe*
e que tornam o cálculo de sombra muito mais lento (3 dias: 466 s com furos, 81 s
sem). A porta de vidro da cozinha muda o resultado: média de janeiro de
27,3 °C para 25,4 °C.

v0.6.5: botões *From EPW* / *From IFC* no painel de clima (latitude,
longitude, elevação e norte do `IfcSite`/`TrueNorth`, fuso do EPW) e *List
Openings* no painel Honeybee, que imprime os nomes das janelas e portas do
modelo para preencher *Windows* e *Glass Doors*.

v0.6.6: zonas excluídas (piscina, varal, garagem aberta) deixam de virar
faces adiabáticas nos vizinhos: uma sonda atrás de cada face interna sem
par testa se cai no sólido de uma zona excluída e, se cai, a face vira
Outdoors e mantém suas janelas e portas. *Undo Site Rotation*: a rotação
que o ArchiCAD grava no `IfcSite` (norte do levantamento) é desfeita na
geometria e vira o *North* do painel. Tipo de zona "claraboia/poço" (sem
cargas, sem ventilação) e piso/forro virtual coincidente entre duas zonas
como fronteira de ar, para poços de luz e pés-direitos duplos divididos em
zonas. Ano completo da casa (13 zonas, varal e garagem excluídos, portas de
vidro): 387 s.

v0.6.7: poço de luz de verdade: a abertura na laje entre o ambiente e a
zona "Claraboia" (limite virtual dos dois lados, detectado pelas fronteiras
do IFC no centro da face, já que a face é dividida pelo `intersect_adjacency`)
vira fronteira de ar, e o topo virtual da zona para o exterior vira abertura
envidraçada. O ArchiCAD exporta o objeto de claraboia como `IfcWindow` sem
geometria nem limite, por isso o vidro é deduzido do limite virtual.

v0.6.8: claraboia ventilada: zona do tipo claraboia fica com a abertura
aberta o ano todo (sem controle de temperatura) e, como o honeybee grava
altura 0 e eficácia de vento 0 para aberturas horizontais (fluxo zero), o
IDF é corrigido com metade da altura da zona como altura de chaminé e
eficácia automática. Lavabo em janeiro: 29,9 → 28,7 °C com `Claraboia=0.25`.

Pendências:

- **Tempo**: a conversão leva ~35 s, quase tudo na mesclagem do contexto
  (uniões booleanas por elemento e por classe). Cachear por elemento ou
  mesclar só o que sobra depois da subtração dos rooms.
- Rodar via `pyenergyplus` in-process com barra de progresso, em vez de
  bloquear o Blender durante a simulação (`subprocess` hoje).
- Programas e horários editáveis no painel; programas por norma (NBR 15575)
  como preset.
- Resultados horários no Period Explorer (temperatura por mês/dia) e gráficos
  2D (hourly plot por ambiente).
- Ainda sem persistência dos resultados no `.blend` além dos números por room.

### Fase 4: Radiance real e Cycles

- **Radiance externo opcional**: com o caminho do Radiance configurado, as
  receitas do `honeybee-radiance` passam a funcionar (daylight factor, annual
  daylight, glare).
- **Cycles como experimento**: converter a matriz de céu numa textura de
  ambiente (equirretangular, radiância por patch), materiais com refletância
  difusa correta e bake de irradiância nas faces. Dá iluminância com
  interreflexão, 100 % nativo. Não é validado como o Radiance e não serve
  para certificação; o teste obrigatório é uma sala de referência (casos CIE
  171) comparada com o Radiance.

## Convenções que evitam bugs

- **Norte**: convenção do Ladybug, graus anti-horários a partir de +Y
  (90 = oeste, 270 = leste). O add-on Sun Position do Blender usa outra.
- **Horário de verão**: não é aplicado (o `Sunpath` do Ladybug só aplica se
  `daylight_saving_period` for definido; aqui nunca é).
- **Hora dos dados**: o Wea gerado do EPW usa HH:30, a mesma convenção do
  gendaymtx.
- **numpy**: usar só funções de módulo (`np.sum`, `np.max`); métodos de
  array quebram quando outro add-on recarrega o numpy no processo.

## Licença e comunidade

AGPL-3.0-or-later, herdada do Ladybug Tools. A ponte Bonsai → Honeybee é o
ponto de contato natural com a comunidade OSArch e com o autor do Bonsai.
