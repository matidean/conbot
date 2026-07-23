# -*- coding: utf-8 -*-
"""
PORTERO
=======
Programita que se queda PRENDIDO en el servidor, esperando el "timbre"
de la pagina. Es el puente entre la pagina (que vive en Netlify y solo
muestra cosas) y el bot (que vive aca en el servidor y hace el trabajo).

Sabe hacer tres cosas, segun lo que le pida la pagina:

  - /traer   -> le devuelve a la pagina las filas de la planilla, para mostrarlas.
  - /cargar  -> larga el bot (el mismo cargar_conversiones.py de siempre).
  - /estado  -> le cuenta a la pagina como va la carga, fila por fila, en vivo.

Todo esta protegido con una clave (CLAVE_PORTERO) para que solo la pagina
de QTM pueda darle ordenes, y no cualquiera de internet.

COMO SE PRENDE (en el servidor):
      python3 portero.py
Y para que quede prendido aunque cierres la consola, mas adelante lo
dejamos como servicio. Por ahora, para probar, con eso alcanza.
"""

import os
import subprocess

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import pandas as pd
from dotenv import load_dotenv

# Leemos el archivo .env para tener a mano la clave del portero.
load_dotenv()

# Reusamos la direccion de la planilla del propio bot, asi no la repetimos.
import cargar_conversiones as bot

app = Flask(__name__)
CORS(app)  # deja que la pagina (que esta en otra direccion) le hable

# La clave que comparten la pagina y el portero. Se lee del archivo .env.
CLAVE_PORTERO = os.getenv("CLAVE_PORTERO", "cambiame")

# La clave para ENTRAR a la pagina (la que se pide al abrirla). Del .env.
CLAVE_ACCESO = os.getenv("CLAVE_ACCESO", "cambiame")

# Donde el bot va escribiendo el avance en vivo mientras trabaja.
ARCHIVO_VIVO = os.path.join("logs", "en_vivo.txt")

# Aca guardamos el bot que esta corriendo (si hay alguno).
_proceso = {"popen": None}


def esta_corriendo():
    """Devuelve True si hay un bot trabajando en este momento."""
    p = _proceso["popen"]
    return p is not None and p.poll() is None


@app.before_request
def exigir_clave_de_acceso():
    """Le pide la clave a cualquiera que abra la pagina. Sin clave, no entra."""
    permiso = request.authorization
    if permiso is None or permiso.password != CLAVE_ACCESO:
        return Response(
            "Necesitas la clave de QTM para entrar.",
            401,
            {"WWW-Authenticate": 'Basic realm="Carga de conversiones QTM"'})


def clave_ok(valor):
    """Chequea que quien golpea la puerta traiga la clave correcta."""
    return valor == CLAVE_PORTERO


@app.route("/")
def pagina():
    """Entrega la pagina, con la clave ya puesta adentro (no la escribe nadie)."""
    f = open("pagina.html", "r", encoding="utf-8")
    html = f.read()
    f.close()
    html = html.replace("__CLAVE__", CLAVE_PORTERO)
    return Response(html, mimetype="text/html")


@app.route("/traer")
def traer():
    """Le devuelve a la pagina las filas de la planilla."""
    if not clave_ok(request.args.get("clave")):
        return jsonify({"error": "no autorizado"}), 401

    df = pd.read_csv(bot.CSV_URL)
    columnas = []
    posibles = ["Comitentecompl", "Comitente", "Moneda",
                "Importe", "TipoConversion", "VALIDADOR"]
    for c in posibles:
        if c in df.columns:
            columnas.append(c)
    df = df[columnas]

    filas = []
    for _, fila in df.iterrows():
        registro = {}
        for c in columnas:
            valor = fila[c]
            if pd.isna(valor):
                registro[c] = ""
            else:
                registro[c] = str(valor)
        filas.append(registro)

    return jsonify({"columnas": columnas, "filas": filas})


@app.route("/cargar", methods=["POST"])
def cargar():
    """Larga el bot. Si ya hay uno corriendo, avisa que esta ocupado."""
    datos = request.get_json(silent=True) or {}
    if not clave_ok(datos.get("clave")):
        return jsonify({"error": "no autorizado"}), 401

    if esta_corriendo():
        return jsonify({"estado": "ocupado"})

    usuario = datos.get("usuario", "(sin identificar)")
    prueba = bool(datos.get("prueba", False))

    orden = ["python3", "-u", "cargar_conversiones.py", "--usuario", usuario]
    if prueba:
        orden.append("--prueba")

    if not os.path.exists("logs"):
        os.makedirs("logs")
    salida = open(ARCHIVO_VIVO, "w", encoding="utf-8")
    _proceso["popen"] = subprocess.Popen(
        orden, stdout=salida, stderr=subprocess.STDOUT)

    return jsonify({"estado": "arranco", "prueba": prueba})


@app.route("/estado")
def estado():
    """Le cuenta a la pagina como va la carga, en vivo."""
    if not clave_ok(request.args.get("clave")):
        return jsonify({"error": "no autorizado"}), 401

    texto = ""
    if os.path.exists(ARCHIVO_VIVO):
        f = open(ARCHIVO_VIVO, "r", encoding="utf-8")
        texto = f.read()
        f.close()

    return jsonify({"corriendo": esta_corriendo(), "texto": texto})


if __name__ == "__main__":
    # Escucha en el puerto 5000, disponible para afuera.
    app.run(host="0.0.0.0", port=5000)
