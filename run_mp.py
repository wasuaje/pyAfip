# -*- coding:utf-8 -*-
import argparse
from afip import _get_afip, MemberInvoice
import logging
from dotenv import load_dotenv
import os
import sys
from datetime import datetime
from decimal import Decimal
from pyafipws.pyfepdf import FEPDF

# logging.basicConfig(level=logging.DEBUG)
logging.basicConfig(level=logging.INFO)
logging.getLogger("pdf").setLevel(logging.DEBUG)
logging.getLogger("pdf").addHandler(logging.StreamHandler())

load_dotenv()


selling_point = os.getenv('selling_point')


def pdf_print_config(is_dev):

    CONF_PDF = dict(
        LOGO="./plantillas/logo.png",
        EMPRESA="LumaSpa 2",
        MEMBRETE1="Moreno 3715 San Martin",
        MEMBRETE2="Buenos Aires",
        CUIT="CUIL 20-95548090-3",
        IIBB="IIBB exento",
        IVA="IVA exento",
        INICIO="Inicio de Actividad: 01/08/2022",
    )

    # inicialización PDF
    fepdf = FEPDF()
    fepdf.CargarFormato("./plantillas/factura.csv")
    fepdf.FmtCantidad = "0.2"
    fepdf.FmtPrecio = "0.2"
    fepdf.CUIT = os.getenv('AFIP_CUIT')

    for k, v in CONF_PDF.items():
        fepdf.AgregarDato(k, v)

    if is_dev:
        fepdf.AgregarCampo("DEMO", 'T', 120, 260, 0, 0, text="DEMOSTRACION",
                           size=70, rotate=45, foreground=0x808080, priority=-1)
        fepdf.AgregarDato("motivos_obs", "Ejemplo Sin validez fiscal")
    return fepdf


def read_csv_data(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        data = f.readlines()
    return data


def create_record(document_number, nombre_cliente, date_comp,
                  amount, process_from, process_to):
    invoice = MemberInvoice(
        document_number=document_number,
        nombre_cliente=nombre_cliente,
        address="San Martin",
        city="San Martin",
        zip_code=1650,
        province="Buenos Aires",
        invoice_number=None,
        invoice_date=date_comp,
        service_date_from=process_from,
        service_date_to=process_to,
        selling_point=selling_point
    )
    invoice.add_item(
        description="Servicio de Manicura/Pedicura/Spa",
        quantity=1,
        amount=amount
    )
    return invoice


def process_invoice_batch(data, process_from, process_to,
                          fecha_facturacion, fepdf, wsfev1, pdf_path):
    invoices = []

    for line in data[1:]:
        try:
            client_name = line.split(',')[0]
            document_number = line.split(',')[1]
            amount = Decimal(line.split(',')[2])
        except IndexError as e:
            print("ERROR:", e)
            print("Linea:", line)
            continue
        date_comp = datetime.strptime(fecha_facturacion, '%d/%m/%Y')
        invoice = create_record(
            document_number, client_name, date_comp,
            amount, process_from, process_to)
        invoices.append(invoice)

    # print(invoices)
    for i in invoices:
        try:
            result = i.autorizar(wsfev1)
            if result:
                print("Ok")
        except Exception as e:
            print(i.__dict__)
            print(e)
        else:
            invoice_date = i.__dict__['header']['fecha_cbte']
            invoice_comp = i.__dict__['header']['cbte_nro']
            i.generate_pdf(fepdf,
                           f'{pdf_path}/{invoice_date}_{invoice_comp}.pdf')


def process_invoice_record(record, process_from, process_to,
                           fecha_facturacion, fepdf, wsfev1, pdf_path):
    # print(f"record {record} pf {process_from} pt {process_to} ff {fecha_facturacion}")
    nombre_cliente = record[0]
    document_number = record[1]
    amount = Decimal(record[2])
    date_comp = datetime.strptime(fecha_facturacion, '%d/%m/%Y')
    # print(document_number, amount, date_comp)
    invoice = create_record(
        document_number, nombre_cliente, date_comp,
        amount, process_from, process_to)

    try:
        result = invoice.autorizar(wsfev1)
        if result:
            print("Ok")
    except Exception as e:
        print(invoice.__dict__)
        print(e)
        result = False
    else:
        invoice_date = invoice.__dict__['header']['fecha_cbte']
        invoice_comp = invoice.__dict__['header']['cbte_nro']
        invoice.generate_pdf(fepdf,
                             f'{pdf_path}/{invoice_date}_{invoice_comp}.pdf')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='CLI para interactuar con PyAfipWS, con un archivo .csv or registro a registro')
    parser.version = '1.0'
    tipo = parser.add_mutually_exclusive_group()
    ambiente = parser.add_mutually_exclusive_group()
    parser.add_argument('--version', action='version')
    parser.add_argument('-f', '--process_from', type=str,
                        help='Fecha de proceso desde, formato (YYYYmmdd)',
                        required=True)
    parser.add_argument('-t', '--process_to', type=str,
                        help='Fecha de proceso hasta, formato (YYYYmmdd)',
                        required=True)
    parser.add_argument('-d', '--fecha_facturacion', type=str,
                        help='Fecha de factura formato (dd/mm/yyyy) +- 10 dias de fecha actual',
                        required=True)
    tipo.add_argument('-p', '--file_path', type=str,
                      help='Ruta al archivo que contiene la data csv')
    tipo.add_argument('-r', '--record', nargs=3,
                      help='Datos de cliente a procesar, \nformato: "Nombre_Cliente" (entre comillas) DNI (sin guiones ni puntos) IMPORTE (9990.00)')

    ambiente.add_argument(
        '--dev', action='store_true', help='Ejectua el script en ambiente de desarrollo')
    ambiente.add_argument(
        '--prod', action='store_true', help='Ejectua el script en ambiente de produccion')

    args = parser.parse_args()
    # variables = vars(args)
    # print(variables)
    # sys.exit(1)

    # this should be parameters
    # process_from = '20230811'
    # process_to = '20230811'
    # fecha_facturacion = '11/10/2023'

    if not args.dev and not args.prod:
        print("Debe seleccionar un ambiente de ejecucion")
        sys.exit(1)

    fepdf = pdf_print_config(True if args.dev else False)

    process_from = args.process_from
    process_to = args.process_to
    fecha_facturacion = args.fecha_facturacion
    private_key = os.getenv('AFIP_PRIVATE_KEY')
    cuit = os.getenv('AFIP_CUIT')

    if args.dev:
        certificate = os.getenv('AFIP_CERTIFICATE_HOMO')
        url_wsaa = os.getenv('url_wsaa_homo')
        url_wsfev = os.getenv('url_wsfev1_homo')
        pdf_path = './pdfs/homo'

    if args.prod:
        certificate = os.getenv('AFIP_CERTIFICATE_PROD')
        url_wsaa = os.getenv('url_wsaa_prod')
        url_wsfev = os.getenv('url_wsfev1_prod')
        pdf_path = './pdfs/prod'

    wsfev1 = _get_afip(certificate=certificate,
                       private_key=private_key,
                       cuit=cuit,
                       url_wsaa=url_wsaa,
                       url_wsfev=url_wsfev)

    if args.file_path:
        data = read_csv_data(args.file_path)

        process_invoice_batch(data, process_from, process_to,
                              fecha_facturacion, fepdf, wsfev1, pdf_path)
    if args.record:
        result = process_invoice_record(args.record, process_from,
                                        process_to, fecha_facturacion,
                                        fepdf, wsfev1, pdf_path)
        if result:
            sys.exit(0)
        else:
            sys.exit(1)


# print(wsfev1.CompUltimoAutorizado(11, selling_point))
# print(wsfev1)
# print(dir(result))
