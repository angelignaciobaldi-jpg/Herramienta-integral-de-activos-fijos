"""Catálogo de empresas del Grupo Petroil (fuente única).

Réplica de la respuesta del SIPP (id + nombre). Es la fuente única para los
combos de las pantallas (registro de activos, automatización SIPP, exportación).
El `id` es el identificador de la empresa en el sistema: sirve para emparejar con
catálogos y para consultar la API.

Solo se guarda id + nombre (datos NO sensibles). El RFC, razón social y demás
campos internos NO se versionan: se consultan en vivo del SIPP/API cuando hagan
falta. Respeta la capitalización tal como la devuelve el SIPP.
"""

from __future__ import annotations

EMPRESAS: list[dict] = [
    {"id": 1, "Empresa": "Abastecedora"},
    {"id": 2, "Empresa": "ACP Combustibles"},
    {"id": 55, "Empresa": "ADMINISTRADORA DE PRESTACION SOCIAL GP"},
    {"id": 24, "Empresa": "AENE PRODUCE SA DE CV"},
    {"id": 37, "Empresa": "AENEKA"},
    {"id": 27, "Empresa": "AEROSERVICIOS AG"},
    {"id": 19, "Empresa": "AMADO SABAS GUZMAN REYNAUD"},
    {"id": 20, "Empresa": "AMBIENTAL TEK RESOURCES"},
    {"id": 6, "Empresa": "Arza"},
    {"id": 11, "Empresa": "Asamaz"},
    {"id": 28, "Empresa": "ASFALTOS"},
    {"id": 12, "Empresa": "Aske"},
    {"id": 4, "Empresa": "Atunes"},
    {"id": 3, "Empresa": "ATUNES Y SARDINAS DE MEXICO"},
    {"id": 40, "Empresa": "BLUE PROPANE"},
    {"id": 17, "Empresa": "CARROZAS DE EPOCA SA DE CV"},
    {"id": 59, "Empresa": "DERIVADOS Y SERVICIOS DE ENERGIA"},
    {"id": 31, "Empresa": "DURAMAS RENUEVALLANTAS"},
    {"id": 52, "Empresa": "ELEKTRON MOTORS AMERICA"},
    {"id": 44, "Empresa": "ELYON LOGISTICS"},
    {"id": 50, "Empresa": "ESTACION DE GAS SANTA MONICA"},
    {"id": 36, "Empresa": "FODEN"},
    {"id": 58, "Empresa": "FUNDACION SEREN"},
    {"id": 39, "Empresa": "Gas Natural Petroil"},
    {"id": 30, "Empresa": "GC MOTORS DE OCCIDENTE"},
    {"id": 38, "Empresa": "IMAA"},
    {"id": 41, "Empresa": "JORGE ALBERTO ELIAS RETES"},
    {"id": 60, "Empresa": "JORGE CASAL GONZALEZ"},
    {"id": 23, "Empresa": "LLANTERA GUZMAN DE GUAMUCHIL"},
    {"id": 16, "Empresa": "Maquinaria Equipos y Construcciones EMMG SA de CV"},
    {"id": 42, "Empresa": "MARCO ANTONIO SANCHEZ ACOSTA"},
    {"id": 53, "Empresa": "Mazaport"},
    {"id": 51, "Empresa": "MAZPARK LOGISTICO"},
    {"id": 43, "Empresa": "MERARID"},
    {"id": 5, "Empresa": "Mexcapital"},
    {"id": 34, "Empresa": "Observatorio Express"},
    {"id": 32, "Empresa": "OCEANICA"},
    {"id": 56, "Empresa": "OPERACIONES TEMATICAS MZT"},
    {"id": 7, "Empresa": "Operadora"},
    {"id": 18, "Empresa": "OPERADORA TURISTICA OBSERVATORIO 1873"},
    {"id": 8, "Empresa": "Petro Smart"},
    {"id": 25, "Empresa": "PETRO SMART COMBUSTIBLES DEL PACIFICO"},
    {"id": 13, "Empresa": "PETROIL BRAND"},
    {"id": 57, "Empresa": "PETROIL ENERGY HOLDING"},
    {"id": 14, "Empresa": "PETROIL HOLDING"},
    {"id": 15, "Empresa": "PETROIL MARINE"},
    {"id": 10, "Empresa": "Petroplazas"},
    {"id": 26, "Empresa": "PETROPLAZAS AEROPUERTO"},
    {"id": 9, "Empresa": "PETROPLAZAS ESTACIONES"},
    {"id": 54, "Empresa": "Quetzaltic"},
    {"id": 29, "Empresa": "SERVICIO EL DURANGUENO"},
    {"id": 47, "Empresa": "SERVICIO J Y J"},
    {"id": 46, "Empresa": "SERVICIOS EDUCATIVOS IMAA"},
    {"id": 45, "Empresa": "SERVICIOS COMPLEMENTARIOS EDUCATIVOS"},
    {"id": 22, "Empresa": "SUPER LLANTAS DEL PACIFICO SA DE CV"},
    {"id": 49, "Empresa": "TAO MOTORS SA DE CV"},
    {"id": 48, "Empresa": "TRASLADOS ROEH"},
    {"id": 21, "Empresa": "TURISMO Y DESARROLLO SHEKINAH"},
]

# Derivados de EMPRESAS (fuente única): lista de nombres para combos e índice
# nombre -> id para emparejar con catálogos y consultas a la API.
NOMBRES_EMPRESAS: list[str] = [e["Empresa"] for e in EMPRESAS]
ID_POR_EMPRESA: dict[str, int] = {e["Empresa"]: e["id"] for e in EMPRESAS}
EMPRESA_POR_ID: dict[int, str] = {e["id"]: e["Empresa"] for e in EMPRESAS}
