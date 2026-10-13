---
description: RECUERDA - Reglas obligatorias antes de tocar el framework de troubleshooting (leer ESTADO_PROYECTO.md y los gotchas, terminal con nombre, git acotado sin evidencia, nada de subir por iniciativa propia)
---

# /RECUERDA ? Checklist OBLIGATORIO antes de tocar nada

> ADAPTADO A ESTE PROYECTO (2026-08-01). El texto original venia copiado de
> OTRO monorepo, de otro usuario: mandaba listar `DEVIN\`, leer un `CAGADAS.md`
> y preguntar en que subproyecto bloquearse. NADA de eso existe aqui, y
> seguirlo al pie de la letra hace que el agente falle en el primer comando.
>
> AQUI hay UN SOLO proyecto y ya esta elegido: la raiz de este repositorio.

Cuando el usuario invoque `/RECUERDA` (o al ABRIR cualquier sesion nueva), Cascade DEBE
declarar que ha leido esto y CUMPLIRLO.

## 1. ANTES DE TOCAR NADA: entender el estado

1. PONER NOMBRE a la terminal (regla 10) con `Cwd` = la carpeta del proyecto:
   ```powershell
   $t = "CASCADE ParraDibiei - checklist"; $Host.UI.RawUI.WindowTitle = $t; [Console]::Write([char]27 + "]0;" + $t + [char]7)
   ```
2. LEER `ESTADO_PROYECTO.md` (estado VIVO: que funciona, que falta, decisiones
   recientes, gotchas) y, si hace falta, `README.md`. Lo deja `/COMMIT` al cerrar.
3. NO empezar a cambiar codigo antes de leer los **gotchas** de
   `ESTADO_PROYECTO.md` y la seccion 12 de aqui: varios son trampas que ya
   costaron tiempo.

## 2. TERMINAL

- En CADA `run_command`: `Cwd` = la raiz del proyecto.
- PROHIBIDO incluir `cd` dentro del comando: la ruta va en `Cwd`.
- Si la salida de un comando sale mezclada o truncada, escribirla a un FICHERO y
  leerla con read_file en vez de fiarse de stdout.

## 3. AMBITO DE TODA OPERACION (incluso solo-lectura)

- `-Path` / `SearchDirectory` / `Cwd` / globs = SIEMPRE la carpeta del proyecto.
- No escanear la carpeta PADRE entera ni el proyecto OEM retirado (~348 MB,
  clave `PASTA_LEGADO`): no aporta nada y contamina las busquedas.

## 4. GIT (repo PROPIO; remote existe y es PUBLICO)

- Esta carpeta es un repo git propio en la rama `master`. **NO es un monorepo.**
- **SI tiene remote** desde 2026-08-11: `origin` -> GitHub **publico**
  (`betobkss/ParraDibiei`). Push es tecnicamente posible, y por eso mismo la
  regla endurece: **nada sale de aqui sin que el usuario lo pida en ese momento**.
  Lo que entra en un repo publico ya salio; no hay deshacer real.
- **REGLA FIJA DEL USUARIO (2026-08-11): "commit solo local siempre".** Todo
  trabajo termina en `git commit` local. `git push` NO se ejecuta nunca por
  iniciativa propia, ni siquiera al invocar `/COMMIT`.
- El riesgo aqui NO es pisar otro proyecto: es **subir evidencia de bancos reales**
  (esquemas, SQL de negocio, hostnames) o datos de conexion de produccion.
- Acotar SIEMPRE excluyendo evidencia; nunca `git add .` a secas:
  `git add -- . ':!output' ':!troubleshooting'`
- REVISAR `git diff --cached --name-only` antes de commitear: que no aparezca
  `output/`, `troubleshooting/<caso>/`, `stringConexao/*.json` ni contrasenas.

## 5. FLUJO DE ENTREGA (solo cuando el usuario lo pida EXPLICITAMENTE)

1. LOCAL: la raiz del proyecto (clave `RAIZ_DO_PROJETO` de
   `caminhos_locais.txt`) - trabajar aqui.
2. SharePoint (destino CONFIRMADO 2026-08-01):
   clave `PASTA_ESPELHO` de `caminhos_locais.txt`
   Comando exacto y avisos en el paso 3 de `/COMMIT`.
3. Git remoto: `origin` (GitHub PUBLICO) existe. Commit local SIEMPRE; **push
   NUNCA** sin peticion explicita del usuario en ese momento.
- No subir/pushear/sincronizar por iniciativa propia. Solo-LOCAL hasta orden expresa.

## 6. CHROME / SELENIUM (evitar SessionNotCreated y no cerrar lo del usuario)

- `--user-data-dir` SIEMPRE con sufijo por PID (`<base>_<pid>`) + auto-purga atexit.
- NUNCA `Get-Process chrome|powerpnt|python | Stop-Process` global (mata sesiones del usuario).
  Solo `Get-Process chromedriver | Stop-Process` o PID concreto que YO lanz?.

## 7. PREFERENCIAS DEL USUARIO

- *** NO ABRIR NI MOSTRAR FUENTES ***: el usuario NO quiere ver codigo fuente. NUNCA abrir
  ficheros de codigo en el editor, ni pegar/mostrar el contenido del codigo en el chat,
  ni dumps de scripts. El usuario GUIA, yo PROGRAMO. Hablar de QUE se hace y POR QUE,
  en lenguaje natural; nada de bloques de codigo salvo que el usuario los pida expresamente.
- IDIOMA: el usuario de ESTE proyecto escribe en **portugues**; responder en portugues.
  (El "responder siempre en espanol" original era del otro usuario/proyecto.)
- "START <x>" = ejecutar directo sin confirmar. "PARA" = taskkill inmediato sin confirmar.
- Si el usuario pide ejecutar algo explicitamente = ejecutar sin pedir confirmacion.
- Outputs (data/logs/screenshots/PPT) -> carpeta espejo del proyecto, no a la carpeta padre.

---
RESUMEN DE 1 LINEA: leer `ESTADO_PROYECTO.md` y los gotchas ANTES de tocar nada;
terminal con nombre y `Cwd` fijo en la carpeta del proyecto; git acotado excluyendo
evidencia; nada de subir/pushear por iniciativa propia.

## 8. COMO EDITAR SIN ABRIR FICHEROS (regla mecanica, EXIGIDA por el usuario)

- El usuario NO quiere que se abra NINGUN fichero en el editor. NUNCA.
- NO usar herramientas de edicion que abran el fichero en el IDE.
- Crear/editar ficheros SIEMPRE desde la TERMINAL (Set-Content / Add-Content / here-string),
  asi el cambio va a disco y el fichero NO se abre en el editor.

## 9. COMANDOS DE CARA AL USUARIO (regla EXIGIDA por el usuario)

- Los comandos/scripts que van a USAR USUARIOS tienen que ser EJECUTABLES POR UN USUARIO
  tal cual: probarlos y validarlos EXACTAMENTE como los teclea el usuario en SU terminal.
  Ejemplo de prueba VALIDA:  .\run.ps1 -old "pregunta" fichero
- PROHIBIDO "demostrar que funciona" con envoltorios que un usuario JAMAS usaria
  (powershell.exe -File ..., redirecciones *> a fichero temporal, trucos de captura, etc.)
  como PRUEBA DE ACEPTACION. La prueba valida es el COMANDO PLANO del usuario.
- La captura a fichero (regla 2) solo vale para VERIFICACION/lectura limpia interna,
  NUNCA como la forma de ejecucion que se entrega al usuario.
- Debe funcionar en Windows PowerShell 5.1 (el de los usuarios), no solo en pwsh 7:
  no pasar textos largos/con espacios/saltos por linea de comandos (usar fichero -f u opciones equivalentes).

## 10. NOMBRE DE LA TERMINAL / CASCADE (regla EXIGIDA: SIEMPRE)

- CADA terminal/Cascade SIEMPRE debe tener SU NOMBRE = el proyecto en el que trabaja. SIEMPRE.
- Nada mas abrir/usar la terminal dedicada, ponerle el titulo (vale en PowerShell 5.1 y 7):
    $t = "CASCADE <proyecto>"; $Host.UI.RawUI.WindowTitle = $t; [Console]::Write([char]27 + "]0;" + $t + [char]7)
- PROHIBIDO dejar terminales llamadas solo "cascade" (sin proyecto): asi es imposible saber
  cual es de cada proyecto y se mezclan/contaminan las salidas entre agentes.
- Si una terminal no tiene nombre de proyecto, NO usarla hasta nombrarla.
- RECORDATORIO (fallo de hoy): el NOMBRE DEL PROYECTO debe verse en la pestana/lado derecho de la terminal SIEMPRE y desde el PRIMER comando de la sesion. Si al abrir NO aparece, ejecutar el comando de titulo ANTES de hacer nada mas.

## 11. POWERPOINT ABIERTO -> NO CERRAR, NO ACTUALIZAR, INFORMAR (regla ABSOLUTA EXIGIDA)

- NUNCA cerrar ni matar PowerPoint (POWERPNT.EXE). JAMAS. Ni global, ni por COM, ni Stop-Process.
- El usuario tiene presentaciones suyas y de trabajo abiertas que NO se deben cerrar nunca.
- Si hay que actualizar/regenerar un PPTX y esta BLOQUEADO porque esta abierto en PowerPoint:
  NO se actualiza. Se INFORMA al usuario (no puedo actualizar X porque lo tienes abierto, cierralo tu) y ya esta.
- Alternativa SOLO si el usuario lo pide: generar el PPT con OTRO nombre. Nunca cerrar el suyo.
- Resumen de 1 linea: PPTX abierto -> NO se actualiza, SE INFORMA y ya esta.

## 12. TRAMPAS ESPECIFICAS DE ESTE FRAMEWORK (leer antes de tocar codigo)

- **`datetime.now()` esta PROHIBIDO en un analizador.** La evidencia es una FOTO;
  toda edad derivada se mide con `base.coletado_em(path)` (mtime del fichero).
  Medir contra el reloj de hoy rompe dos cosas: el informe MIENTE sobre el pasado
  al reabrir un caso viejo, y `MELHORIA.py --corpus` acusa regresiones FALSAS que
  crecen cada dia (paso: 11 al dia). Ya corregido en `saude.py` y `stats.py`.
- **El corpus de regresion es el guardian: no aceptar baseline a la ligera.**
  `MELHORIA.py --corpus` compara 41 casos reales. Si acusa cambios, entender POR
  QUE antes de `--aceptar`, y dejar la justificacion en el mensaje del commit:
  `output/melhoria/baseline.json` NO va al git, asi que el commit es el unico
  sitio donde queda el razonamiento.
- **`pytest -q` dos veces se vuelve `-qq` y borra la linea de resumen.** El
  `pyproject.toml` ya trae `addopts = "-q"`; quien llame a pytest por subproceso
  necesita `-o addopts=` o leera "0 tests" en una suite verde.
- **La marca no se duplica.** `troubleshoot/marca.py` es la fuente unica; hay un
  test que compara el dibujo del panel con el del dashboard. Nada de `<img>` ni
  CDN: el panel corre offline y el informe se manda por e-mail.
- **Nada de red ni recursos externos en el HTML.** Panel y dashboard tienen que
  abrir sin internet.
- **`--advisor` NO es read-only** (crea/ejecuta tuning tasks y consume Tuning
  Pack). Es opt-in explicito, esta prohibido en la bateria de homologacion y el
  panel lo bloquea a proposito.
- **Produccion solo por evidencia ya grabada.** La rutina de mejora NUNCA
  reconecta a un banco de produccion ni escribe en la carpeta del caso; conectar
  solo contra homologacion.
- **Contrasena: SIEMPRE se teclea, en cada ejecucion.** Ya esta implementado
  (02/08/2026): no hay `--password`, ni lectura de `ORA_PASSWORD`, ni fichero.
  El unico sitio que pregunta es `troubleshoot/senha.py`; el panel responde por
  el `stdin` del hijo (`troubleshoot/dialogo.py`). Unica excepcion consciente:
  `mcp/server.py`, que corre sin terminal. Hay tests que barren el codigo y
  reprueban cualquier CLI que vuelva al atajo comodo - si uno de esos falla, es
  la regla funcionando, no un test molesto: no lo debilites.
- **El corpus de regresion esta VACIO desde el 05/08/2026** (el usuario pidio
  zerar la interfaz). Mientras no haya casos recolectados, `MELHORIA.py --corpus`
  no prueba nada: no lo cites como evidencia de que un cambio es correcto.
