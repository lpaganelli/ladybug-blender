# Ladybug Tools para Blender

*English: [README.md](README.md)*

Porte das ferramentas de análise ambiental do [Ladybug Tools](https://www.ladybug.tools/)
para o Blender (4.2+ / 5.x), empacotado como **Extension**.

*Guia de análise: [docs/GUIA.md](docs/GUIA.md) · exportar o IFC do ArchiCAD: [docs/IFC_ARCHICAD.md](docs/IFC_ARCHICAD.md) · roadmap: [docs/ROADMAP.md](docs/ROADMAP.md)*

As bibliotecas Python puras do Ladybug (`ladybug-core`, `ladybug-geometry`,
`ladybug-radiance`, `ladybug-comfort`, `ladybug-display`) vão embutidas como
wheels. As partes que no Ladybug original dependem do Radiance foram
reimplementadas nativamente:

| Ladybug original | Neste porte |
|---|---|
| `gendaymtx` (matriz de céu Perez) | `core/skymatrix.py` em numpy (Perez all-weather + sol nos 3 patches mais próximos, igual ao gendaymtx) |
| `rcontrib` (interseção raio × geometria) | `core/intersect.py` com `mathutils.bvhtree.BVHTree` |
| Malhas coloridas do Rhino/Grasshopper | Color attributes + material, legendas como objetos de texto |

## Funcionalidades (v0.1)

Painel **Sidebar (N) > Ladybug** na 3D Viewport:

- **Weather & Location**: carregar EPW, ler localização, ângulo de Norte; ou localização manual.
- **Analysis Period**: período de análise (mês/dia/hora início e fim, timestep).
- **Sun Path**: analemas horários, arcos diários (dia 21 de cada mês), bússola, pontos
  de sol do período; luz Sun posicionada para data/hora; animação de um dia por keyframes.
- **Solar Studies** (na malha ativa, com contexto de sombreamento):
  - *Direct Sun Hours* — horas de sol direto por face (ou vértice).
  - *Incident Radiation* — radiação incidente acumulada (kWh/m²) ou irradiância média (W/m²),
    com céu Perez do EPW ou céu claro ASHRAE, Tregenza (145) ou Reinhart (577).
- **Climate Graphics** no cursor 3D: Sky Dome (3D ou projetado), Radiation Rose, Wind Rose.
- **Legend**: paleta (colorsets do Ladybug), faixa customizada, número de segmentos.

Os estudos **nunca alteram o objeto original**. Cada estudo gera uma cópia da
geometria avaliada (modificadores aplicados) deslocada pelo *Offset* ao longo das
normais, organizada em:

```
Ladybug / LB Results / <objeto> (LB) / <objeto> · Sun Hours   (+ legenda)
                                       <objeto> · Radiation   (+ legenda)
```

Ocultar a subcoleção esconde todos os resultados daquele objeto. Rodar o mesmo
estudo de novo substitui o resultado anterior; com a cópia ativa, o estudo resolve
o original automaticamente. Os valores ficam como atributos da cópia
(`LB Sun Hours`, `LB Radiation`, cor em `LB Color`), utilizáveis em Geometry
Nodes, shaders ou exportação. *Clear Active* / *Clear All* removem resultados.

## Instalação

```powershell
.\build.ps1 -Install
```

Isso valida, empacota `dist\ladybug_tools-<versão>.zip` e instala no repositório
`user_default` do Blender indicado (padrão: Blender 5.2.1 LTS em `C:\SOFTWARES\Blender Builds`).
Para outro Blender: `.\build.ps1 -Blender "C:\caminho\blender.exe" -Install`.
Ou instale o zip manualmente em *Edit > Preferences > Get Extensions > Install from Disk*.

Arquivos EPW: <https://climate.onebuilding.org/> ou <https://energyplus.net/weather>.

## Uso rápido

1. Carregue um EPW (ou digite latitude/longitude/fuso).
2. Posicione o cursor 3D e clique **Draw Sun Path**.
3. Selecione a malha de estudo (ativa) e os objetos de contexto; escolha o período;
   clique **Direct Sun Hours** ou **Incident Radiation**. A viewport muda para
   *Solid > Attribute* para mostrar as cores.
4. Para gráficos climáticos, posicione o cursor 3D e use Sky Dome / Radiation Rose / Wind Rose.

## Compatibilidade com outros add-ons

O add-on legado **DeepBump** recarrega o numpy dentro do processo (apaga `numpy` do
`sys.modules` e importa a cópia própria, 2.4.4). Depois disso, métodos de redução de
arrays (`arr.sum()`, `arr.max()`) falham em qualquer add-on com
`TypeError: ... not '_NoValueType'`. Este porte usa só as funções de módulo
(`np.sum`, `np.max`, `np.add.reduce`), que continuam funcionando, mas outros
add-ons numéricos podem quebrar; se isso incomodar, desative o DeepBump.

Objetos gerados pelo add-on (legendas, sun path, rosas) recebem a propriedade
`ladybug_generated` e nunca entram como contexto de sombreamento.

## Convenções

- Unidades do Blender = metros. Eixo +Y = Norte (ajustável em *North*).
- O céu é calculado uma vez por (EPW, período, densidade) e fica em cache na sessão.
- Direct Sun Hours: horário local do EPW; use *Solar Time* no sun path para hora solar.

## Desempenho (Blender 5.x, malha de 2500 faces + 5 blocos)

| Estudo | Tempo |
|---|---|
| Direct Sun Hours, 1 dia | 0,1 s |
| Direct Sun Hours, ano inteiro (4371 posições de sol) | 5,4 s |
| Incident Radiation, ano, Tregenza | 0,8 s |
| Matriz de céu anual | 0,4 s |

## Testes

```powershell
# EPW sintético + validação física da matriz de céu (energia conservada)
$py = "C:\SOFTWARES\Blender Builds\stable\blender-5.2.1-lts.9e2066aef7ef\5.2\python\bin\python.exe"
$env:PYTHONPATH = "<pasta com os pacotes ladybug>"   # pip install --target
& $py tests\make_test_epw.py tests\test_sao_paulo.epw
& $py tests\test_skymatrix.py tests\test_sao_paulo.epw

# Teste ponta a ponta no Blender (modo dev, sem instalar)
blender -b --factory-startup --python tests\test_headless.py -- <pasta ladybug> tests\test_sao_paulo.epw
blender -b tests\out\test_result.blend --python tests\render_result.py

# Teste da extensão instalada
blender -b --python tests\test_installed.py -- tests\test_sao_paulo.epw
```

## Estrutura

```
ladybug_tools/
  blender_manifest.toml   # manifesto da Extension (wheels, permissões)
  __init__.py             # registro
  props.py                # propriedades da cena (Scene.ladybug)
  ui.py                   # painéis
  core/skymatrix.py       # matriz de céu Perez (numpy)
  core/intersect.py       # BVH ray casting, pontos de estudo
  core/blender_geom.py    # Ladybug geometry/legend/compass -> objetos Blender
  core/cache.py           # cache de EPW / Wea / céu
  ops/weather.py          # EPW
  ops/sunpath.py          # sun path, luz Sun, animação
  ops/studies.py          # direct sun hours, incident radiation
  ops/roses.py            # sky dome, radiation rose, wind rose
  wheels/                 # ladybug-* (AGPL-3.0), click, colorama
```

## Próximos passos sugeridos

- Conforto: UTCI / PMV via `ladybug-comfort` (já embutido), gráfico psicrométrico.
- Estudos de vista (view factor / sky view) reutilizando `intersect.py`.
- Sombreamento por Geometry Nodes, benefício/prejuízo (benefit/harm) com temperatura de balanço.
- Honeybee (EnergyPlus/Radiance) exigiria os binários externos; fora do escopo deste porte nativo.

## Licença

AGPL-3.0-or-later (herdada das bibliotecas Ladybug Tools).
