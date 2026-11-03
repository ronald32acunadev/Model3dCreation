# TRELLIS.2 darkness experiments

Lower loss is closer to the input. loss = per-pixel ΔE + 20·|ln(luma ratio)|.

| Config | Metal | n | loss | pixel ΔE | mean ΔE | luma ratio | hist corr | back/front |
|---|---|---|---|---|---|---|---|---|
| 1024_cascade | exposure none | fp32 | scaled | 7 | 23.28 | 19.79 | 8.02 | 1.15 | 0.55 | 0.93 |
| engine-baseline | scaled | 5 | 23.65 | 18.32 | 8.26 | 1.23 | 0.60 | 1.03 |
| engine-baseline | raw | 5 | 23.78 | 18.07 | 7.80 | 0.96 | 0.77 | 1.02 |
| 1024_cascade | exposure none | fp16 | raw | 21 | 24.35 | 20.73 | 8.01 | 0.97 | 0.69 | 0.95 |
| 1024_cascade | exposure none | fp32 | zero | 7 | 24.46 | 20.30 | 8.70 | 1.22 | 0.49 | 0.93 |
| 512 | exposure none | fp16 | scaled | 7 | 24.52 | 21.43 | 7.56 | 0.99 | 0.57 | 1.07 |
| 1024_cascade | exposure none | fp32 | raw | 7 | 24.75 | 19.86 | 8.68 | 0.90 | 0.67 | 0.90 |
| 1024_cascade | exposure levels | fp16 | raw | 21 | 24.85 | 21.53 | 7.81 | 0.95 | 0.71 | 0.97 |
| 1024_cascade | exposure none | fp16 | scaled | 21 | 24.94 | 21.10 | 8.40 | 1.17 | 0.54 | 0.96 |
| engine-baseline | zero | 5 | 25.14 | 19.01 | 9.29 | 1.32 | 0.51 | 1.03 |
| 1024_cascade | exposure levels | fp16 | scaled | 21 | 25.36 | 21.80 | 8.35 | 1.14 | 0.55 | 0.98 |
| 512 | exposure none | fp16 | zero | 7 | 25.67 | 21.77 | 8.35 | 1.04 | 0.52 | 1.07 |
| 1024_cascade | exposure none | fp16 | zero | 21 | 26.19 | 21.61 | 9.27 | 1.24 | 0.47 | 0.97 |
| 512 | exposure none | fp16 | raw | 7 | 26.37 | 21.42 | 8.37 | 0.82 | 0.65 | 1.06 |
| 1024_cascade | exposure levels | fp16 | zero | 21 | 26.54 | 22.28 | 9.22 | 1.21 | 0.48 | 0.98 |
| 1024_cascade | exposure gamma | fp16 | raw | 21 | 26.98 | 21.54 | 10.52 | 1.32 | 0.41 | 0.95 |
| 1024_cascade | exposure gamma | fp16 | scaled | 21 | 28.45 | 22.23 | 11.79 | 1.39 | 0.33 | 0.94 |
| 1024_cascade | exposure gamma | fp16 | zero | 21 | 29.13 | 22.55 | 12.32 | 1.41 | 0.31 | 0.94 |

## Best per image

| Image | Config | Seed | Metal | loss | luma ratio |
|---|---|---|---|---|---|
| axe-snow | 1024_cascade | exposure none | fp16 | s7 | zero | 19.03 | 1.01 |
| knight | 1024_cascade | exposure levels | fp16 | s123 | raw | 18.48 | 1.02 |
| penguin | 1024_cascade | exposure none | fp16 | s7 | raw | 24.44 | 1.20 |
| person-photo | 512 | exposure none | fp16 | s42 | raw | 26.08 | 0.99 |
| police-night | 1024_cascade | exposure levels | fp16 | s123 | raw | 11.14 | 1.02 |
| space-marine | 512 | exposure none | fp16 | s42 | raw | 17.78 | 0.98 |
| varsity | 1024_cascade | exposure levels | fp16 | s42 | raw | 14.97 | 1.01 |

## Raw decoder metallic (median) per run

| Run | metallic p50 | share above 1 | seconds |
|---|---|---|---|
| axe-snow__s123__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 89 |
| axe-snow__s123__1024_cascade__levels__fp16 | 0.017 | 0.007 | 89 |
| axe-snow__s123__1024_cascade__none__fp16 | 0.393 | 0.0 | 91 |
| axe-snow__s42__1024_cascade__gamma__fp16 | 0.197 | 0.057 | 95 |
| axe-snow__s42__1024_cascade__levels__fp16 | 0.922 | 0.117 | 90 |
| axe-snow__s42__1024_cascade__none__fp16 | 0.999 | 0.297 | 95 |
| axe-snow__s42__1024_cascade__none__fp32 | 1.0 | 0.285 | 124 |
| axe-snow__s42__512__none__fp16 | 0.0 | 0.013 | 41 |
| axe-snow__s7__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 90 |
| axe-snow__s7__1024_cascade__levels__fp16 | 0.556 | 0.0 | 91 |
| axe-snow__s7__1024_cascade__none__fp16 | 0.283 | 0.001 | 93 |
| knight__s123__1024_cascade__gamma__fp16 | 0.053 | 0.0 | 87 |
| knight__s123__1024_cascade__levels__fp16 | 0.612 | 0.0 | 86 |
| knight__s123__1024_cascade__none__fp16 | 0.543 | 0.001 | 86 |
| knight__s42__1024_cascade__gamma__fp16 | 0.607 | 0.002 | 88 |
| knight__s42__1024_cascade__levels__fp16 | 1.0 | 0.331 | 87 |
| knight__s42__1024_cascade__none__fp16 | 0.999 | 0.3 | 88 |
| knight__s42__1024_cascade__none__fp32 | 0.999 | 0.289 | 88 |
| knight__s42__512__none__fp16 | 1.0 | 0.41 | 42 |
| knight__s7__1024_cascade__gamma__fp16 | 0.558 | 0.0 | 88 |
| knight__s7__1024_cascade__levels__fp16 | 0.999 | 0.295 | 89 |
| knight__s7__1024_cascade__none__fp16 | 0.999 | 0.222 | 89 |
| penguin__s123__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 85 |
| penguin__s123__1024_cascade__levels__fp16 | 0.003 | 0.0 | 85 |
| penguin__s123__1024_cascade__none__fp16 | 0.0 | 0.0 | 85 |
| penguin__s42__1024_cascade__gamma__fp16 | 0.424 | 0.0 | 85 |
| penguin__s42__1024_cascade__levels__fp16 | 0.457 | 0.0 | 84 |
| penguin__s42__1024_cascade__none__fp16 | 0.424 | 0.0 | 85 |
| penguin__s42__1024_cascade__none__fp32 | 0.423 | 0.0 | 86 |
| penguin__s42__512__none__fp16 | 0.0 | 0.0 | 40 |
| penguin__s7__1024_cascade__gamma__fp16 | 0.147 | 0.0 | 84 |
| penguin__s7__1024_cascade__levels__fp16 | 0.314 | 0.0 | 83 |
| penguin__s7__1024_cascade__none__fp16 | 0.147 | 0.0 | 84 |
| person-photo__s123__1024_cascade__gamma__fp16 | 0.001 | 0.003 | 140 |
| person-photo__s123__1024_cascade__levels__fp16 | 0.0 | 0.0 | 136 |
| person-photo__s123__1024_cascade__none__fp16 | 0.0 | 0.0 | 146 |
| person-photo__s42__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 70 |
| person-photo__s42__1024_cascade__levels__fp16 | 0.0 | 0.003 | 74 |
| person-photo__s42__1024_cascade__none__fp16 | 0.236 | 0.0 | 72 |
| person-photo__s42__1024_cascade__none__fp32 | 0.236 | 0.0 | 95 |
| person-photo__s42__512__none__fp16 | 0.0 | 0.0 | 40 |
| person-photo__s7__1024_cascade__gamma__fp16 | 0.0 | 0.008 | 123 |
| person-photo__s7__1024_cascade__levels__fp16 | 0.0 | 0.0 | 123 |
| person-photo__s7__1024_cascade__none__fp16 | 0.0 | 0.006 | 141 |
| police-night__s123__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 73 |
| police-night__s123__1024_cascade__levels__fp16 | 0.581 | 0.0 | 74 |
| police-night__s123__1024_cascade__none__fp16 | 0.811 | 0.0 | 73 |
| police-night__s42__1024_cascade__gamma__fp16 | 0.156 | 0.0 | 73 |
| police-night__s42__1024_cascade__levels__fp16 | 0.983 | 0.118 | 73 |
| police-night__s42__1024_cascade__none__fp16 | 1.0 | 0.31 | 73 |
| police-night__s42__1024_cascade__none__fp32 | 1.0 | 0.293 | 75 |
| police-night__s42__512__none__fp16 | 1.0 | 0.351 | 40 |
| police-night__s7__1024_cascade__gamma__fp16 | 0.096 | 0.0 | 73 |
| police-night__s7__1024_cascade__levels__fp16 | 0.529 | 0.0 | 73 |
| police-night__s7__1024_cascade__none__fp16 | 0.998 | 0.14 | 73 |
| space-marine__s123__1024_cascade__gamma__fp16 | 0.294 | 0.004 | 108 |
| space-marine__s123__1024_cascade__levels__fp16 | 0.92 | 0.005 | 107 |
| space-marine__s123__1024_cascade__none__fp16 | 0.867 | 0.001 | 108 |
| space-marine__s42__1024_cascade__gamma__fp16 | 0.448 | 0.001 | 108 |
| space-marine__s42__1024_cascade__levels__fp16 | 0.999 | 0.282 | 108 |
| space-marine__s42__1024_cascade__none__fp16 | 0.999 | 0.233 | 108 |
| space-marine__s42__1024_cascade__none__fp32 | 0.999 | 0.218 | 110 |
| space-marine__s42__512__none__fp16 | 0.467 | 0.0 | 44 |
| space-marine__s7__1024_cascade__gamma__fp16 | 0.49 | 0.014 | 106 |
| space-marine__s7__1024_cascade__levels__fp16 | 0.999 | 0.221 | 105 |
| space-marine__s7__1024_cascade__none__fp16 | 0.916 | 0.0 | 108 |
| varsity__s123__1024_cascade__gamma__fp16 | 0.153 | 0.0 | 63 |
| varsity__s123__1024_cascade__levels__fp16 | 0.377 | 0.0 | 63 |
| varsity__s123__1024_cascade__none__fp16 | 0.385 | 0.026 | 64 |
| varsity__s42__1024_cascade__gamma__fp16 | 0.0 | 0.0 | 73 |
| varsity__s42__1024_cascade__levels__fp16 | 0.223 | 0.0 | 64 |
| varsity__s42__1024_cascade__none__fp16 | 0.0 | 0.0 | 63 |
| varsity__s42__1024_cascade__none__fp32 | 0.0 | 0.0 | 64 |
| varsity__s42__512__none__fp16 | 0.0 | 0.0 | 38 |
| varsity__s7__1024_cascade__gamma__fp16 | 0.372 | 0.0 | 64 |
| varsity__s7__1024_cascade__levels__fp16 | 1.0 | 0.368 | 64 |
| varsity__s7__1024_cascade__none__fp16 | 0.639 | 0.018 | 64 |
