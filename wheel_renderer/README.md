# wheel_renderer — mandala com labels deslocadas

Renderer isolado (CP1). **Ainda não integrado ao pipeline** — ver `../ESTADO.md`.

    cd api/wheel_renderer && SP=. python3 battery.py

`renderer.py` constrói o wheel do zero e reusa apenas os `<defs>` do Kerykeion
(`kerykeion_defs.svg`). Não depende de `draw_modern`.

Semântica: o **tick** na longitude real é a posição astrológica; a **label** é
anotação deslocada, ligada por **leader line**. Ver `../ESTADO.md` seção 2.

As asserções (`assert_render`) são o critério de aceite — quebram loud, não
silenciam. Rodar a bateria antes de aceitar qualquer bump do Kerykeion.
