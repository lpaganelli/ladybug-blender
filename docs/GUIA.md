# Guia de análise solar e climática no Blender

Este guia explica os conceitos por trás do add-on **Ladybug Tools para Blender**,
o que cada parte do painel faz e quando usar cada análise. Ele foi escrito para
quem está começando em análise ambiental de edificações.

## 1. Conceitos

**EPW (EnergyPlus Weather).** Arquivo com 8760 horas (um ano típico) de dados
medidos numa estação meteorológica: temperatura, umidade, vento, nuvens e
radiação solar. Não é "o ano passado": é um ano *típico* montado a partir de
décadas de medições. Toda análise com clima real parte dele. Sem EPW, o add-on
ainda desenha o sol (geometria pura) e pode usar um céu claro teórico.
Fontes: <https://climate.onebuilding.org/> e <https://energyplus.net/weather>.

**Posição do sol.** Depende só de latitude, longitude, fuso e data/hora. Dois
ângulos: *altitude* (quanto acima do horizonte) e *azimute* (direção na
bússola). O *Sun Path* é o desenho de todas essas posições ao longo do ano.

**Radiação solar tem três partes:**

- *Direta (DNI)*: vem do disco solar em linha reta. É a que projeta sombra nítida.
- *Difusa (DHI)*: espalhada pelo céu e pelas nuvens, vem de toda a abóbada.
  Num dia nublado é tudo o que há.
- *Refletida pelo chão*: fração da global que o solo devolve (*Ground
  Reflectance*: 0,2 para grama e concreto, 0,6 para neve).

**Matriz de céu.** Em vez de calcular o sol hora a hora, a abóbada é dividida
em 145 "azulejos" (patches Tregenza) ou 577 (Reinhart). Para cada patch soma-se
quanta energia veio dali ao longo do período. O *Sky Dome* é o mapa disso. A
*Incident Radiation* de uma superfície é a soma dos patches que ela "enxerga",
ponderada pelo ângulo de incidência.

**Sensores.** Cada face (ou vértice) da malha de estudo é um ponto de medição.
Malha mais subdividida = mais resolução e mais tempo. Um modificador Remesh ou
Subdivision no objeto é a forma prática de controlar isso, sem aplicá-lo.

## 2. Os dois estudos

### Direct Sun Hours

Responde: *quantas horas de sol direto este ponto recebe no período?* É
geometria pura, não usa o EPW (só a localização). Use para:

- insolação mínima em quartos e áreas externas (por exemplo "2 h de sol no
  solstício de inverno");
- sombreamento de um vizinho sobre um terreno, ou o efeito de um beiral;
- onde uma piscina ou horta pega sol.

Dica: rode com período curto e representativo. 21 de junho (pior caso de
inverno no Brasil), 21 de dezembro (verão) e 21 de março. O ano inteiro dá
uma média que esconde as estações.

### Incident Radiation

Responde: *quanta energia solar (kWh/m²) chega aqui?* Usa o EPW, então nuvens
e clima local entram na conta. Use para:

- dimensionar fotovoltaico ou aquecedor solar: qual água do telhado recebe
  mais energia e em que inclinação;
- ganho térmico de fachadas: onde vale brise, vidro de controle solar ou
  vegetação;
- comparar orientações de um lote antes de implantar.

Marque *Average Irradiance (W/m²)* para potência média em vez de energia
acumulada, útil para comparar períodos de tamanhos diferentes.

Ordem de grandeza para conferir resultados: em São Paulo, um plano horizontal
recebe cerca de 1600 a 1900 kWh/m² por ano com EPW real. Fachada norte fica em
torno de 60 a 70 % disso, sul em 25 a 35 %.

## 3. Opções que mais mudam o resultado

| Opção | O que faz | Sugestão |
|---|---|---|
| Analysis Period | Define a pergunta | Ano para energia; um dia ou estação para insolação |
| Timestep | Posições de sol por hora | 1/h basta; 4/h só para animação |
| Context | Quem faz sombra | *Selected* para controle; *All Visible* em modelos grandes |
| Self Shading | O objeto se sombreia | Ligado |
| Offset | Distância dos sensores acima da superfície | 1 cm; aumente em malhas com dobras finas |
| Per Vertex | Resultado interpolado por vértice | Apresentação; por face para números |
| Sky | EPW (clima real) ou ASHRAE clear sky | Céu claro = pior caso de ganho térmico ou sem EPW |
| High Density | 577 patches em vez de 145 | Superfícies muito inclinadas, brises |
| North | Ângulo do norte a partir de +Y, anti-horário | Ajuste aqui, não gire a cena |

## 4. Climate Graphics

Desenhados no cursor 3D, independem da geometria. Servem para entender o clima
antes de projetar.

- **Sky Dome.** De onde vem a energia do céu no período. *Stereographic*
  achata em 2D (formato clássico de relatório). *Total / Direct / Diffuse*
  separam as componentes: se o difuso domina, o clima é nublado e a
  orientação importa menos.
- **Radiation Rose.** O mesmo dado resumido por orientação de fachada: cada
  seta é quanta radiação recebe uma parede virada para lá. *Tilt* inclina a
  superfície (20° simula um telhado).
- **Wind Rose.** Frequência de vento por direção, colorida pela velocidade
  (ou temperatura, ou umidade). Ventilação natural cruzada, posição de
  aberturas e barreiras. Filtre o período para ver o vento de verão.

## 5. Sun Path e luz

- *Draw Sun Path*: analemas (curvas em 8 = mesma hora ao longo do ano),
  arcos diários do dia 21 de cada mês, bússola e pontos de sol do período.
- *Set Sun Light*: aponta uma luz Sun real para a data e hora, para render
  no EEVEE/Cycles com a sombra correta.
- *Animate*: keyframes do sol ao longo de um dia.

## 6. Fluxo de trabalho sugerido

1. Carregue o EPW da cidade e desenhe o Sun Path.
2. Rode Sky Dome e Radiation Rose: de onde vem o calor e para onde apontar o
   que quer aquecer ou proteger.
3. Direct Sun Hours no terreno e nas fachadas em 21/jun e 21/dez, com os
   vizinhos como contexto: valida implantação e insolação.
4. Incident Radiation anual em telhado (fotovoltaico) e fachadas (proteção
   solar).
5. Use o seletor *Show* para alternar estudos e as subcoleções para comparar
   alternativas.

## 7. O que o add-on não faz

- Temperatura interna e consumo de energia (Honeybee + EnergyPlus).
- Iluminação natural em lux (Radiance).
- Reflexões entre edifícios: só a do chão, de forma simplificada.

Para partido e implantação, o que existe cobre a maior parte das decisões.
