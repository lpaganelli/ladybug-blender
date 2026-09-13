# Exportar o IFC do ArchiCAD para o Honeybee

O add-on lê pouca coisa do IFC. Um translator enxuto exporta em segundos e
gera um arquivo pequeno; com "tudo ligado" a exportação de uma casa térrea
consumia toda a memória e travava o ArchiCAD.

## O que o add-on usa

| Dado do IFC | Para quê |
|---|---|
| `IfcSpace` com geometria 3D | cada zona vira um Room (sólido fechado) |
| `IfcRelSpaceBoundary` | diz o que é cada face do room: elemento, interno/externo, aberturas |
| `IfcWall`, `IfcSlab`, `IfcRoof`, `IfcColumn` | contexto de sombreamento (beirais, muros, o que sobra além das zonas) |
| `IfcWindow`, `IfcDoor` | Aperture / Door na face que os contém |
| `IfcMaterialLayerSet` + `Pset_MaterialThermal` | construções opacas com condutividade, densidade e calor específico |
| `IfcSite` (latitude, longitude) e `TrueNorth` | localização e norte do projeto |

Não usa quantidades, classificações, propriedades de elementos, mobiliário,
vegetação, terreno nem vistas 2D.

## Translator sugerido (ArchiCAD 29)

*Arquivo > Interoperabilidade > IFC > Tradutores IFC*, crie um translator
para exportação (IFC4, Reference View ou Design Transfer View) e escolha
estas predefinições. Confira no fim de cada janela que a predefinição
aparece em "Tradutores relacionados": uma predefinição criada mas não
selecionada no translator não tem efeito.

**Filtro do modelo:** *Elementos Construtivos com Zonas*. Deixa de fora
mobiliário, terreno, vegetação e transporte. O `IfcSite` sai sempre.

**Conversão de geometria:** *Extrusões* (Paramétrico, extrudado/revolvido).
Não é preciso BREP exato. Paredes com junções saem como CSG e, desde a
v0.6.4, o add-on une as faces coplanares por união booleana, então isso não
gera fragmentos.

**Conversão de dados:**

- Classificações, propriedades de elemento, parâmetros: desligados.
- *Propriedades e Classificações do Material de Construção*: ligado (é de
  onde vem o `Pset_MaterialThermal`).
- *Exportar Propriedades IFC*: *Todas as Propriedades IFC* (garante o Pset
  térmico sem depender do mapeamento).
- *Quantidades Base IFC*: desligado. É o item mais pesado da exportação e o
  add-on calcula os volumes por conta própria.
- *Confinamento do Espaço IFC* e *Limites do Espaço IFC*: ligados. Sem os
  limites o add-on não sabe classificar as faces.

## Zonas

- Uma zona por ambiente, do piso ao forro, com o vegetal ligado na vista 3D
  de onde se exporta. Zonas que não aparecem no 3D não são exportadas.
- Crie zonas também nos **áticos** (entre a laje e o telhado): sem elas a
  laje troca calor direto com o exterior e o resultado fica frio demais. O
  add-on reconhece o nome "ático", "forro" ou "attic" e trata a zona como
  não ocupada e muito ventilada.
- Áreas **descobertas ou abertas** que existem como zona no ArchiCAD (piscina,
  varal, garagem aberta) devem ir no campo *Exclude* do painel. Uma zona
  excluída não vira ambiente, e as faces dos vizinhos voltadas para ela
  passam a ser exteriores, com as janelas e portas que dão para ela. Uma
  porta de correr para o varal continua opaca a menos que esteja em *Glass
  Doors* (a regra automática só olha portas marcadas externas no IFC).
- Onde uma laje serve a vários ambientes (um ático sobre três quartos) o
  add-on divide a face por ambiente antes de emparelhar.

## Claraboias e poços de luz

Uma claraboia sobre um ambiente, atravessando o ático, precisa existir como
volume no IFC. Duas formas:

1. **Zona alta**: estenda a zona do ambiente (o Lavabo) até o telhado, faça
   um furo no polígono da zona do ático nesse trecho e coloque a claraboia
   como janela de telhado. As paredes do poço devem ser paredes, para
   separarem o poço do ático.
2. **Zona própria**: uma zona "Claraboia" do forro do ambiente ao telhado,
   sobre a abertura na laje. O add-on reconhece o nome (claraboia, poço,
   shaft, skylight) como zona sem cargas nem ventilação; a parte do forro
   onde não há laje (a abertura) vira fronteira de ar entre as duas zonas,
   e o topo da zona, se não houver elemento nenhum ali (limite virtual para
   o exterior), vira uma abertura envidraçada, a claraboia. O resto do forro
   continua como laje para o ático. Testado: Lavabo + zona Claraboia de
   0,73 m² sobre a abertura. A claraboia é fixa por padrão; uma claraboia
   levantada, que ventila pela fresta, entra no campo *Windows* como
   `Claraboia=0.25` (fração = perímetro × fresta ÷ área do vidro; 5 cm de
   fresta em 85 × 85 cm dá 0,25) e fica aberta o ano todo, sem o controle
   de temperatura das janelas.

Sem isso, a abertura no forro não tem informação de vidro nem de volume, e o
ambiente fica com um forro fechado para o ático.

## Portas e janelas

- `IfcWindow` vira abertura envidraçada; `IfcDoor` vira porta opaca, exceto
  as **portas de vidro**: portas externas cujo tipo se chama "de correr",
  "pivotante", "vidro" (ou sliding/glass) são tratadas como vidro, com ganho
  solar e ventilação. O campo *Glass Doors* do painel corrige a lista pelo
  nome: `PA06, PA09` força vidro, `-PA10` força opaca (um portão de garagem
  de correr, por exemplo).
- A fração que abre vale para janelas e portas de vidro, pelo nome, no campo
  *Windows* da caixa EnergyPlus: `JA01=0, JA02=0.5, PA09=0.5`.
- Uma janela encostada em outra (bandeira sobre a porta-janela) é
  ligeiramente encolhida para não sobrepor; uma porta que cruza a divisão
  entre duas faces da mesma parede é recortada em cada face.
- O IFC não diz se a porta tem vidro (o `Pset_DoorCommon` do ArchiCAD sai
  vazio), por isso a regra por nome.

## Contexto de sombreamento

Entram como sombra as partes de paredes, lajes, telhados, pilares e vigas
que ficam fora das zonas (beirais, muros, pergolados). O que está dentro de
uma zona (vigas do telhado dentro do ático, forro de gesso) é descartado.
Aberturas nas paredes de contexto são fechadas: para sombrear, a parede é
tratada como opaca. O objeto `HB Context` pode ser editado à mão antes de
simular.

## Norte e localização

*Opções > Preferências do Projeto > Localização do Projeto*: latitude,
longitude e o campo **Norte**. O norte sai no `TrueNorth` do IFC e o add-on
o coloca no campo *North* do painel, que vale para os estudos de sol e para
o EnergyPlus. Se o IFC vier sem norte (TrueNorth = +Y), digite o ângulo no
painel: graus anti-horários a partir de +Y, convenção do Ladybug.

Coordenadas de mapa (`IfcMapConversion`, ponto de levantamento) não são
necessárias para a análise.

Com um EPW carregado, a localização do painel é a do arquivo de clima. Os
botões *From EPW* / *From IFC* do painel *Weather & Location* trocam entre
as duas (o IFC também traz o norte; o fuso horário fica o do EPW).

Posição do modelo no translator (*Definir a posição do modelo IFC por*).
O ArchiCAD 29 **não grava o `TrueNorth`** (fica sempre +Y). O norte só sai
como rotação do `IfcSite` quando se exporta com *Ponto de Origem e Origem do
Projeto*. Com a opção *Undo Site Rotation* do painel (ligada por padrão) o
add-on desfaz essa rotação, deixa a geometria nos eixos do projeto (mesma
orientação do FBX e do DWG) e passa o ângulo para o campo *North*. Com a
opção desligada a geometria fica girada com o norte em +Y e *North* = 0.
Com *Origem do Projeto apenas* nada carrega o norte: digite-o no painel
(o valor da *Localização do Projeto*, graus anti-horários a partir de +Y).
Coordenadas saem certas nos dois casos.

## Nomes das janelas e portas

Os campos *Windows* e *Glass Doors* usam os nomes dos elementos no ArchiCAD
(ID: `JA01`, `PA09`), que saem sempre no IFC. Não é preciso exportar
*Parâmetros Porta-Janela*. O botão *List Openings* imprime no Info os nomes
encontrados no modelo, com tipo, área e ambientes.

## Conferindo o arquivo

Depois de converter, o painel mostra `N rooms, N faces, N apertures, ...` e
a contagem de condições de contorno. O esperado para uma casa fechada:
`Outdoors` nas fachadas e cobertura, `Ground` nos pisos térreos, `Surface`
nos pares internos (paredes entre ambientes, laje sob o ático) e poucos ou
nenhum `Adiabatic`. Muitos `Adiabatic` indicam faces internas que não
encontraram o vizinho: zona faltando de um lado, ou parede mais espessa que
0,5 m.
