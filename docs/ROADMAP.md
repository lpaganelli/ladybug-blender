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
  encosta em dois vizinhos): dividir a face pela projeção do vizinho
  (`Face3D.coplanar_split`) antes de emparelhar, ou mover ambas para o eixo
  da parede.
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

Pendências:

- **Tempo**: o custo é o sombreamento do contexto (telhado em centenas de
  peças). Mesclar planos coplanares entre elementos, descartar lajes internas
  e peças pequenas; já sem reflexões e com sombras a cada 30 dias.
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
