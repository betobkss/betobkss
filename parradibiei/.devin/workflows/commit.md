---
description: /COMMIT - Consolidar, documentar (README + ESTADO_PROYECTO.md para el proximo /RECUERDA), verificar suite y corpus, y commitear acotado excluyendo evidencia de bancos reales
---

# /COMMIT - Cerrar y entregar el framework de troubleshooting

> ADAPTADO A ESTE PROYECTO (2026-08-01). El texto anterior venia copiado de
> OTRO monorepo, de otro usuario, con estructura `DEVIN/<proyecto>` y un
> SharePoint ajeno. NADA de eso existe aqui.
>
> REALIDAD DE ESTE PROYECTO:
> - Carpeta: la raiz de este repositorio (clave `RAIZ_DO_PROJETO` de
>   `caminhos_locais.txt` cuando haga falta la ruta absoluta).
> - Repo git **propio de esta carpeta** (opcion B del `GIT_INFO.md`), NO monorepo.
> - Rama: `master`. **SI hay remote** (`origin`, GitHub **publico**) desde
>   2026-08-11 — el texto anterior decia "sin remote" y ya no es cierto.
>   Push posible tecnicamente, pero **NUNCA por iniciativa propia**: repo
>   publico + `/COMMIT` autoriza COMMIT, no PUSH. Preguntar siempre.
> - Por tanto: NO hay `git add DEVIN/<proyecto>`, NO hay rama `feat/` sobre
>   `origin/main`, NO hay PR. El acotado se hace excluyendo evidencia (ver paso 4).

Precondicion: estar trabajando en esta carpeta. TODO con Cwd = esa carpeta.
Invocar /COMMIT ES la orden explicita para subir (regla 5): SP y git solo cuando el usuario lo pide.

## 0. Confirmar proyecto y ambito (solo lectura)
// turbo
   ```powershell
   git branch --show-current
   git status --short
   git diff --name-only
   ```
- Rama esperada: `master` (repo propio de esta carpeta). No hay monorepo ni ramas de otros agentes.
- Si aparecen cambios que NO reconozco de la sesion: PARAR y preguntar antes de acotar.

## 1. Documentar README.md + ESTADO_PROYECTO.md
- Actualizar `README.md`.
- Contenido minimo: que hace, requisitos, COMO SE EJECUTA con el COMANDO PLANO del usuario
  (regla 9: ejecutable por un usuario en PowerShell 5.1), parametros, entradas y salidas.
- **Los numeros NO se escriben a mano.** README, ESTADO_PROYECTO y PLATAFORMA
  llevan un bloque `<!-- NUMEROS:INICIO/FIM -->` generado del repositorio:
   ```powershell
   .\.venv\Scripts\python.exe -m troubleshoot.numeros_doc
   ```
  `tests/test_numeros_doc.py` reprueba el commit si el bloque quedo atrasado
  (por eso el hook lo pilla solo). Escribirlos a mano fue lo que dejo "1538
  tests / 74 checks" publicado teniendo 1662 y 78.

- Actualizar `ESTADO_PROYECTO.md`.
  Es el fichero de ESTADO que el PROXIMO /RECUERDA leera para entender el proyecto y su situacion.
- Contenido: fecha de actualizacion, ESTADO ACTUAL (que funciona), QUE QUEDA PENDIENTE,
  ultimos cambios/decisiones, ficheros y rutas clave, comandos planos del usuario, gotchas conocidos.
- Mantenerlo CORTO y al dia: es el resumen VIVO del proyecto, no un changelog largo.

## 2. Consolidar
- Limpiar temporales del proyecto: `__pycache__`, `*.pyc`, `.pytest_cache`, `.ruff_cache`.
- ANTES de commitear, verificar que la suite sigue verde y que el corpus de
  regresion no acusa cambios inesperados:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest -q
   .\.venv\Scripts\python.exe MELHORIA.py --corpus
   ```
- Si el corpus acusa cambios: NO commitear a ciegas. O es un bug introducido, o
  es una correccion legitima - y si es correccion, la JUSTIFICACION va en el
  mensaje del commit (ver paso 4).

## 3. Subir a SharePoint (SOLO si el usuario lo pide explicitamente)
- Los caminos absolutos de esta maquina NO estan aqui: viven en
  `caminhos_locais.txt` (local, no versionado - ver `caminhos_locais.txt.modelo`).
  LEE ese fichero y usa las claves `RAIZ_DO_PROJETO` y `PASTA_ESPELHO`.
- Si el fichero no existe, o `PASTA_ESPELHO` esta vacia: **PARA y pregunta**.
  Nunca adivines el destino.
- **SUSTITUYE los marcadores por los caminos reales antes de ejecutar.** Pegar
  el comando con `<RAIZ_DO_PROJETO>` literal falla con ERROR 123 - ya paso.
- Excluir SIEMPRE evidencia y entorno:
   ```powershell
   robocopy "<RAIZ_DO_PROJETO>" "<PASTA_ESPELHO>" /E /NFL /NDL /R:2 /W:2 /XD .venv __pycache__ .git .pytest_cache .ruff_cache dist output troubleshooting node_modules /XF *.pyc
   ```
- **`/E` y NO `/MIR`.** Los dos copian el arbol; `/MIR` ademas **BORRA en destino
  lo que no esta en origen**, y en este destino vive algo que NO esta en origen:
  el `historico.bundle` (copia de los commits, ver GIT_INFO.md). Con `/MIR` el
  espejo destruye la unica copia del historial - medido el 2026-09-18, el
  dry-run lo listaba para borrar. `/E` lo deja en paz (sale como
  `*Arquivo EXTRA`, que es informativo).
- Si algun dia hace falta `/MIR` de verdad, corre primero el dry-run (`/L`) y
  lee la lista de EXTRA: cada uno seria borrado.
- Codigo de salida de robocopy: 0-7 es EXITO (1 = ficheros copiados, 2 =
  ficheros EXTRA en destino, 3 = ambos). Solo >=8 es error - no confundirlo con
  el convenio habitual de exit code.
- El espejo cubre FICHEROS, no el historial: `.git` esta excluido a proposito
  (OneDrive sincronizando el interior de `.git` puede corromper el repo). El
  historial va aparte, como bundle - ver GIT_INFO.md.
- Ojo: la carpeta de destino esta en OneDrive; escribir dispara subida a la nube.
- **Si va `stringConexao\*.json`**: llevan host/puerto/servicio/usuario de
  produccion. Al SharePoint corporativo si; al git NO (sigue [A CONFIRMAR]).

## 4. Git ACOTADO (excluir evidencia, no "excluir otros proyectos")
- Aqui el riesgo NO es pisar otro proyecto (es repo propio): es **subir evidencia
  de bancos reales** (nombres de esquema, SQL de negocio, hostnames). `output/` y
  `troubleshooting/` estan en `.gitignore`; reforzar el acotado igualmente:
   ```powershell
   git add -- . ':!output' ':!troubleshooting'
   git diff --cached --name-only
   ```
- REVISAR la lista staged antes de commitear. Que NO aparezca:
  `stringConexao/*.json` (host/puerto/servicio/usuario de produccion), nada de
  `output/`, nada de `troubleshooting/<caso>/`, ninguna contrasena.
- Mensaje de commit: explicar el **porque**, no solo el que. En particular, si
  cambio el baseline del corpus (`output/melhoria/baseline.json`), el mensaje
  DEBE justificar por que no es una regresion - ese fichero no va al git, asi que
  el commit es el unico sitio donde queda el razonamiento.
   ```powershell
   git commit -F <fichero-con-el-mensaje>
   ```
- **REGLA FIJA DEL USUARIO (2026-08-11): el commit es SIEMPRE SOLO LOCAL.**
  `/COMMIT` termina en `git commit`. **NUNCA** `git push`, aunque `origin`
  (GitHub PUBLICO) exista y el push sea tecnicamente posible. Lo que entra en un
  repo publico ya salio; no hay deshacer real. Solo se pushea si el usuario lo
  pide **explicitamente en ese momento**, y aun entonces confirmando antes que el
  diff no lleva hostname de la bateria, `stringConexao/*.json` ni evidencia.

## 5. Aprender para otros agentes
- Guardar en memoria (create_memory) lo NUEVO y reutilizable: decisiones de diseno, rutas,
  comandos de usuario validados, gotchas, dependencias. Asi otros agentes lo reutilizan.
- Si lo aprendido es una REGLA GLOBAL, anadirlo tambien a
  `.devin/workflows/recuerda.md` de este proyecto.

---
RESUMEN 1 LINEA: documentar README + ESTADO_PROYECTO.md, verificar suite y corpus,
consolidar, git acotado excluyendo evidencia (commit SIEMPRE solo local, nunca push), y
aprender lo reutilizable en memoria.
