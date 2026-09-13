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
- A piscina é ignorada pelo nome (campo *Exclude* do painel).
- Onde uma laje serve a vários ambientes (um ático sobre três quartos) o
  add-on divide a face por ambiente antes de emparelhar.

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
Projeto*: a geometria chega girada com o norte em +Y e o campo *North* = 0
está correto. Com *Origem do Projeto apenas* a geometria sai como está no
ArchiCAD (mesma orientação do FBX e do DWG), mas nada carrega o norte:
digite-o no painel (o valor da *Localização do Projeto*, graus anti-horários
a partir de +Y). Coordenadas saem certas nos dois casos.

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
