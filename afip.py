import os
from decimal import Decimal

from pyafipws.wsaa import WSAA
from pyafipws.wsfev1 import WSFEv1
from pyafipws.pyfepdf import FEPDF

# from django.conf import settings

from dotenv import load_dotenv

load_dotenv()

CACHE = "/tmp/pyafip-cache-real"

PDF_PATH = "/tmp"

INVOICE_TYPE = 11  # factura C

IVA_CODES = {
    Decimal('10.5'): 4,
    Decimal(0): 3,
    Decimal(21): 5,
    Decimal(27): 6,
}


def _get_afip(certificate, private_key, url_wsaa, url_wsfev, cuit):
    """Build and authenticate AFIP structure."""
    # AFIP init
    wsaa = WSAA()
    wsfev1 = WSFEv1()

    # get access ticket (token y sign)
    # certificate = settings.AFIP['auth_cert_path']
    # private_key = settings.AFIP['auth_key_path']
    # certificate = os.getenv('AFIP_CERTIFICATE')
    # private_key = os.getenv('AFIP_PRIVATE_KEY')

    if not os.path.exists(certificate):
        raise ValueError(
            "Auth certificate can not be found (got {!r})".format(certificate))
    if not os.path.exists(private_key):
        raise ValueError(
            "Auth key can not be found (got {!r})".format(private_key))

    msg = f"""
    Authenciation AFIP using:
        certificate: {certificate}
        private_key: {private_key}
        url_wsaa: {url_wsaa}
        CACHE: {CACHE}
    """

    print(msg)
    ta = wsaa.Autenticar(
        "wsfe", certificate, private_key, wsdl=url_wsaa, cache=CACHE, debug=True)
    wsfev1.Cuit = cuit
    wsfev1.SetTicketAcceso(ta)
    wsfev1.Conectar(CACHE, url_wsfev)
    return wsfev1


def verify_service(selling_point):
    """Basic initial check that everything works with AFIP."""
    wsfev1 = _get_afip()
    last_auth_invoice = wsfev1.CompUltimoAutorizado(
        INVOICE_TYPE, selling_point)
    if int(last_auth_invoice) == 0:
        raise ValueError("Bad last auth invoice (AFIP is misbehaving)")
    print("Last authorized invoice", last_auth_invoice)
    res = wsfev1.CompConsultar(INVOICE_TYPE, last_auth_invoice, selling_point)
    print("Current status", repr(res))
    return int(last_auth_invoice)


class _BaseInvoice:
    """Base invoice for standard operations."""

    def __init__(self, config):
        self.header = config.copy()
        self.header.update(dict(
            # lot of defaults
            imp_total=Decimal(0), imp_tot_conc=Decimal(0), imp_neto=Decimal(0),
            imp_trib=Decimal(0), imp_op_ex=Decimal(0), imp_iva=Decimal(0),
            moneda_id='PES', moneda_ctz=1.000,
            pais_dst_cmp=200, id_impositivo='Consumidor Final',
            motivo_obs="", cae="", resultado='', fch_venc_cae="",
        ))
        self.cmp_asocs = []
        self.items = []
        self.ivas = {}

    def add_item(self, description, quantity, amount):
        """Add the billed service."""
        item = dict(
            u_mtx=123456,
            cod_mtx=1234567890123,
            codigo="",
            ds=description,
            qty=quantity,
            umed=7,
            bonif=0.00,
            despacho=u'Nº 123456',
        )
        subtotal = amount * quantity
        self.header["imp_neto"] += subtotal

        iva_id = IVA_CODES[self.iva_rate]
        item["iva_id"] = iva_id

        if self.iva_rate:
            # discriminate IVA if type A / M
            iva_liq = (subtotal * self.iva_rate /
                       100).quantize(Decimal("0.01"))
            self._add_iva(iva_id, subtotal, iva_liq)
            self.header["imp_neto"] += subtotal
            self.header["imp_iva"] += iva_liq
            if self.header["tipo_cbte"] in (1, 2, 3, 4, 5, 34, 39, 51, 52, 53, 54, 60, 64):
                # FIXME: this should be verified
                item["precio"] = amount / (1 + self.iva_rate / 100)
                item["imp_iva"] = amount * self.iva_rate / 100
            else:
                # no discriminar IVA si es clase B (importe final iva incluido)
                item["precio"] = amount
                item["imp_iva"] = (amount * self.iva_rate /
                                   100).quantize(Decimal("0.01"))
        else:
            iva_liq = 0
            item["precio"] = amount
            item["imp_iva"] = None
            if self.iva_rate is None:
                self.header["imp_tot_conc"] += subtotal     # no gravado
            else:
                # self.header["imp_op_ex"] += subtotal        # exento
                self.header["imp_op_ex"] = 0        # exento

        item["importe"] = subtotal + iva_liq
        self.header["imp_total"] += subtotal + iva_liq
        self.items.append(item)

    def _add_iva(self, iva_id, base_imp, importe):
        iva = self.ivas.setdefault(
            iva_id, dict(iva_id=iva_id, base_imp=Decimal(0), importe=Decimal(0)))
        iva["base_imp"] += base_imp
        iva["importe"] += importe

    def autorizar(self, wsfev1):
        "Prueba de autorización de un comprobante (obtención de CAE)"
        # busco proximo comprobante
        cbte_nro = wsfev1.CompUltimoAutorizado(
            self.header["tipo_cbte"], self.header["punto_vta"])
        # print("    last authorized invoice", cbte_nro,
        #       self.header["tipo_cbte"], self.header["punto_vta"])

        self.header["cbte_nro"] = int(cbte_nro) + 1

        self.header["cbt_desde"] = self.header["cbte_nro"]
        self.header["cbt_hasta"] = self.header["cbte_nro"]

        wsfev1.CrearFactura(**self.header)

        # agrego un comprobante asociado (solo notas de crédito / débito)
        for cmp_asoc in self.cmp_asocs:
            wsfev1.AgregarCmpAsoc(**cmp_asoc)

        # agrego el subtotal por tasa de IVA (iva_id 5: 21%):
        for iva in self.ivas.values():
            wsfev1.AgregarIva(**iva)

        # llamo al websevice para obtener el CAE:
        wsfev1.CAESolicitar()

        for obs in wsfev1.Observaciones:
            print("WARNING", obs)

        if wsfev1.ErrMsg:
            raise RuntimeError(wsfev1.ErrMsg)

        authorized_ok = wsfev1.Resultado == "A" and bool(
            wsfev1.CAE) and bool(wsfev1.Vencimiento)

        self.header["resultado"] = wsfev1.Resultado
        self.header["cae"] = wsfev1.CAE
        self.header["fch_venc_cae"] = wsfev1.Vencimiento
        # print(self.__dict__)
        return authorized_ok

    def generate_pdf(self, fepdf, filepath):
        """Generate the invoice image."""

        fepdf.CrearFactura(**self.header)

        # completo campos extra del header:
        fepdf.EstablecerParametro(
            "localidad_cliente", self.header.get("localidad", ""))
        fepdf.EstablecerParametro(
            "provincia_cliente", self.header.get("provincia", ""))

        # imprimir leyenda "Comprobante Autorizado" (constatar con WSCDC!)
        fepdf.EstablecerParametro("resultado", self.header["resultado"])

        # detalle de artículos:
        for item in self.items:
            fepdf.AgregarDetalleItem(**item)

        # agrego remitos y otros comprobantes asociados:
        for cmp_asoc in self.cmp_asocs:
            fepdf.AgregarCmpAsoc(**cmp_asoc)

        # agrego el subtotal por tasa de IVA (iva_id 5: 21%):
        for iva in self.ivas.values():
            fepdf.AgregarIva(**iva)

        # armar el PDF:
        fepdf.CrearPlantilla(papel="A4", orientacion="portrait")
        fepdf.ProcesarPlantilla(num_copias=1, lineas_max=24, qty_pos='izq')
        fepdf.GenerarPDF(archivo=filepath)


class MemberInvoice(_BaseInvoice):
    """Specific invoice with hardcoded values for members."""

    iva_rate = Decimal(0)
    # iva_rate = None

    def __init__(
        self, document_number, nombre_cliente, address, city, zip_code, province,
        invoice_number, invoice_date, service_date_from, service_date_to,
            selling_point):
        config = dict(
            # defaults for this invoice type
            tipo_cbte=INVOICE_TYPE,
            tipo_doc=96,
            punto_vta=selling_point,
            concepto=3,  # 1 products 2 services 3 products and services

            # for this invoice in particular
            nombre_cliente=nombre_cliente if nombre_cliente else "Consumidor Final",
            nro_doc=document_number,
            domicilio_cliente=address,
            localidad="{} ({})".format(city, zip_code),
            provincia=province,
            cbte_nro=invoice_number,
            fecha_cbte=invoice_date.strftime("%Y%m%d"),
            fecha_serv_desde=service_date_from,
            fecha_serv_hasta=service_date_to,
            fecha_venc_pago=invoice_date.strftime("%Y%m%d"),
        )
        super().__init__(config)


class MassiveProductSellingInvoice(_BaseInvoice):
    """Specific invoice for thouse massive sellings (as t-shirts)."""

    iva_rate = Decimal(21)

    def __init__(self, invoice_number, invoice_date, selling_point):
        config = dict(
            tipo_cbte=INVOICE_TYPE,
            tipo_doc=99,
            nro_doc=0,
            punto_vta=selling_point,
            concepto=1,  # products

            # for this invoice in particular
            cbte_nro=invoice_number,
            fecha_cbte=invoice_date.strftime("%Y%m%d"),
        )
        super().__init__(config)
