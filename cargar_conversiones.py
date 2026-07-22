# -*- coding: utf-8 -*-
"""
CARGA MASIVA DE CONVERSIONES / REINVERSIONES EN COHEN
=====================================================
Este programa hace lo mismo que tu notebook, pero ordenado para correr
en el servidor:

  1. Lee la clave y el codigo 2FA desde un archivo aparte (.env), no del codigo.
  2. Entra a Cohen (usuario + clave + codigo 2FA).
  3. Lee las filas desde la planilla de Google (la misma de siempre).
  4. Carga fila por fila en el formulario de transferencias.
  5. Anota un registro de cada corrida: quien, cuando, y como salio cada fila.

COMO SE USA (desde la consola del servidor):
  Prueba sin guardar nada (recomendado antes de cargar en serio):
      python cargar_conversiones.py --prueba --usuario nombre@qtmcapital.com.ar

  Carga de verdad:
      python cargar_conversiones.py --usuario nombre@qtmcapital.com.ar

Lo unico que se rompe cuando Cohen cambia su pagina son los "casilleros"
marcados abajo en el bloque SELECTORES. Es lo unico que hay que retocar.
"""

import os
import sys
import time
import argparse
from datetime import datetime

import pandas as pd
import pyotp
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException


# =====================================================================
# CONFIGURACION (lo que puede cambiar sin tocar el resto del programa)
# =====================================================================

# La planilla de siempre, publicada como CSV. Es la misma del notebook.
CSV_URL = ("https://docs.google.com/spreadsheets/d/e/"
           "2PACX-1vReXAvIylshRDWiJUv95yBcXJTQp9axtAL6uanacs7fqM7vuhpEMy"
           "2qDqJChWv9cNZOXzJ6U2NcJZPm/pub?gid=0&output=csv")

LOGIN_URL = "https://byma.cohen.com.ar/#/auth/login"
TRANSFER_URL = "https://byma.cohen.com.ar/#/transferencia/add"

# Carpeta donde se guardan los registros de cada corrida.
CARPETA_LOGS = "logs"

# El codigo 2FA de Cohen. Casi todos los autenticadores usan estos valores
# (6 digitos, cambia cada 30 segundos). Si el login fallara por el codigo,
# esto es lo unico a revisar.
TOTP_DIGITOS = 6
TOTP_SEGUNDOS = 30


# =====================================================================
# SELECTORES  <<< ESTO ES LO QUE HAY QUE RETOCAR SI COHEN CAMBIA LA PAGINA
# =====================================================================
# Son las "direcciones" de cada casillero del formulario. Si un dia el bot
# deja de andar, casi seguro es porque Cohen movio algo y hay que corregir
# aca. Estan tal cual las tenias andando en tu notebook.

SEL_USUARIO      = (By.ID, "FRM_LOGIN_INP_USU")
SEL_CLAVE        = (By.ID, "FRM_LOGIN_INP_CONT")
SEL_BTN_LOGIN    = (By.ID, "FRM_LOGIN_BTN_INISES")
SEL_INPUT_2FA    = (By.XPATH, '//*[@id="loginModal"]/div/div/div[2]/div[1]/form/div[1]/input')
SEL_BTN_2FA      = (By.XPATH, '/html/body/div[1]/div/div/div/div/div[2]/div[1]/form/div[2]/button')

SEL_COMITENTE    = (By.XPATH, '/html/body/div[1]/div/div/form/fieldset/div[2]/div/input')
SEL_MONEDA       = (By.XPATH, '/html/body/div[1]/div/div/form/fieldset/div[3]/div/wrap-button/div/select')
SEL_IMPORTE      = (By.XPATH, '/html/body/div[1]/div/div/form/fieldset/div[4]/div/input')
SEL_TIPO         = (By.XPATH, '/html/body/div[1]/div/div/form/fieldset/div[5]/div/select')
SEL_BTN_GUARDAR  = (By.XPATH, '/html/body/div[1]/div/div/form/div[3]/div/button[2]')
SEL_BTN_CONFIRMAR = (By.ID, "ok")

# Cuando termina el login, Cohen redirige a esta pantalla. La esperamos
# para saber que entramos bien.
SENAL_LOGIN_OK = "/alertaGraficoSinOperacion/list"


# =====================================================================
# REGISTRO (el "papelito" de cada corrida)
# =====================================================================

def abrir_registro(usuario):
    """Crea el archivo de registro de esta corrida y devuelve por donde escribir."""
    if not os.path.exists(CARPETA_LOGS):
        os.makedirs(CARPETA_LOGS)
    momento = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = os.path.join(CARPETA_LOGS, "carga_" + momento + ".txt")
    f = open(ruta, "a", encoding="utf-8")
    f.write("CORRIDA DE CARGA DE CONVERSIONES\n")
    f.write("Usuario que ejecuto: " + str(usuario) + "\n")
    f.write("Fecha y hora       : " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
    f.write("-" * 60 + "\n")
    f.flush()
    print("Registro guardado en:", ruta)
    return f


def anotar(f, texto):
    """Escribe una linea en el registro y tambien la muestra en pantalla."""
    print(texto)
    f.write(texto + "\n")
    f.flush()


# =====================================================================
# LOGIN
# =====================================================================

def esperar_ventana_2fa(segundos, borde=2):
    """
    Evita usar el codigo 2FA justo cuando esta por vencer.
    Si faltan pocos segundos para que cambie, espera a la ventana nueva.
    """
    falta = segundos - (int(time.time()) % segundos)
    if falta <= borde:
        time.sleep(falta + 0.5)


def hacer_login(web, usuario, clave, semilla_2fa):
    """Entra a Cohen: usuario, clave y codigo 2FA."""
    totp = pyotp.TOTP(semilla_2fa, digits=TOTP_DIGITOS, interval=TOTP_SEGUNDOS)

    web.get(LOGIN_URL)

    WebDriverWait(web, 30).until(EC.presence_of_element_located(SEL_USUARIO))
    web.find_element(*SEL_USUARIO).send_keys(usuario)
    web.find_element(*SEL_CLAVE).send_keys(clave)
    web.find_element(*SEL_BTN_LOGIN).click()

    # Ingresar el codigo 2FA (con reintentos por si el cartel se recarga).
    intento = 0
    while intento < 3:
        try:
            casillero = WebDriverWait(web, 15).until(
                EC.element_to_be_clickable(SEL_INPUT_2FA))
            esperar_ventana_2fa(TOTP_SEGUNDOS)
            casillero.clear()
            casillero.send_keys(totp.now())
            break
        except StaleElementReferenceException:
            intento += 1

    WebDriverWait(web, 15).until(
        EC.element_to_be_clickable(SEL_BTN_2FA)).click()

    # Confirmamos que entramos bien.
    WebDriverWait(web, 20).until(EC.url_contains(SENAL_LOGIN_OK))
    time.sleep(2)


# =====================================================================
# CARGA DE UNA FILA
# =====================================================================

def cargar_fila(web, fila, guardar):
    """
    Carga una sola fila en el formulario.
    Si guardar=False, completa todo pero NO aprieta guardar (modo prueba).
    Devuelve un texto con el resultado.
    """
    comitente = fila["Comitente"]
    moneda = fila["Moneda"]
    importe = fila["Importe"]
    tipo = fila["TipoConversion"]

    # Comitente
    WebDriverWait(web, 10).until(EC.visibility_of_element_located(SEL_COMITENTE))
    campo = web.find_element(*SEL_COMITENTE)
    campo.clear()
    campo.send_keys(str(comitente))
    time.sleep(1)
    campo.send_keys(Keys.TAB)
    WebDriverWait(web, 5).until(
        EC.presence_of_element_located(
            (By.XPATH, "//ul[contains(@class, 'uib-typeahead') or "
                       "contains(@class, 'dropdown-menu')]//li")))
    campo.send_keys(Keys.TAB)

    # Moneda
    Select(web.find_element(*SEL_MONEDA)).select_by_visible_text(moneda)

    # Importe
    campo_importe = web.find_element(*SEL_IMPORTE)
    campo_importe.clear()
    campo_importe.send_keys(str(importe))

    # Tipo de conversion (con reintentos por si la pagina se refresca)
    espera = WebDriverWait(web, 10)
    intento = 0
    while intento < 3:
        try:
            elem = espera.until(EC.element_to_be_clickable(SEL_TIPO))
            Select(elem).select_by_visible_text(tipo)
            break
        except StaleElementReferenceException:
            intento += 1
            time.sleep(1)
            if intento == 3:
                raise

    if not guardar:
        return "PRUEBA (no se guardo)"

    # Guardar y aceptar el cartel de confirmacion
    web.find_element(*SEL_BTN_GUARDAR).click()
    time.sleep(1)
    try:
        WebDriverWait(web, 5).until(
            EC.element_to_be_clickable(SEL_BTN_CONFIRMAR)).click()
        time.sleep(2)
        return "OK (guardada)"
    except Exception:
        return "GUARDADA sin cartel de confirmacion (revisar)"


# =====================================================================
# PROGRAMA PRINCIPAL
# =====================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prueba", action="store_true",
                        help="Recorre y completa todo pero NO guarda nada.")
    parser.add_argument("--usuario", default="(sin identificar)",
                        help="Mail de quien ejecuta, para el registro.")
    args = parser.parse_args()

    # Leer credenciales del archivo .env (nunca en el codigo)
    load_dotenv()
    cohen_usuario = os.getenv("COHEN_USUARIO")
    cohen_clave = os.getenv("COHEN_CLAVE")
    cohen_2fa = os.getenv("COHEN_2FA")
    if not (cohen_usuario and cohen_clave and cohen_2fa):
        print("ERROR: faltan datos en el archivo .env "
              "(COHEN_USUARIO, COHEN_CLAVE, COHEN_2FA).")
        sys.exit(1)

    f = abrir_registro(args.usuario)
    if args.prueba:
        anotar(f, ">>> MODO PRUEBA: no se guarda ninguna fila <<<")

    # Leer la planilla
    df = pd.read_csv(CSV_URL)
    if "Comitente" in df.columns:
        df["Comitente"] = pd.to_numeric(df["Comitente"], errors="coerce").astype("Int64")
    anotar(f, "Filas leidas de la planilla: " + str(len(df)))
    anotar(f, "-" * 60)

    # Abrir Chrome. En el servidor va "sin ventana" (headless).
    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--window-size=1920,1080")
    web = webdriver.Chrome(service=Service(), options=opciones)

    ok = 0
    error = 0
    try:
        anotar(f, "Entrando a Cohen...")
        hacer_login(web, cohen_usuario, cohen_clave, cohen_2fa)
        anotar(f, "Login correcto.")

        web.get(TRANSFER_URL)
        time.sleep(2)

        numero = 0
        for _, fila in df.iterrows():
            numero += 1
            etiqueta = "Fila " + str(numero) + " - comitente " + str(fila["Comitente"])
            try:
                resultado = cargar_fila(web, fila, guardar=not args.prueba)
                anotar(f, etiqueta + " -> " + resultado)
                ok += 1
            except Exception as e:
                anotar(f, etiqueta + " -> FALLO: " + str(e).split("\n")[0])
                error += 1
                # Sacamos una foto de la pantalla para ver que paso.
                try:
                    foto = os.path.join(CARPETA_LOGS,
                                        "error_fila_" + str(numero) + ".png")
                    web.save_screenshot(foto)
                    anotar(f, "   (foto del error: " + foto + ")")
                except Exception:
                    pass
    finally:
        anotar(f, "-" * 60)
        anotar(f, "RESUMEN: " + str(ok) + " ok, " + str(error) + " con error.")
        f.close()
        web.quit()


if __name__ == "__main__":
    main()
