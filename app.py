from datetime import datetime
from flask import Flask, redirect, url_for, render_template, request, jsonify, send_from_directory
from flask_restx import Resource, Api

from audioop import add
from doctest import master
from email import header
from json.tool import main
from os import stat
from grpc import ServicerContext, StatusCode
from pyqrllib.pyqrllib import str2bin, hstr2bin, bin2hstr
from binascii import hexlify, a2b_base64
from qrl.crypto.xmss import XMSS

from qrl.core import config
from qrl.core.AddressState import AddressState
from qrl.core.OptimizedAddressState import OptimizedAddressState
from qrl.core.PaginatedBitfield import PaginatedBitfield
from qrl.core.Block import Block
from qrl.core.TransactionMetadata import TransactionMetadata
from qrl.core.ChainManager import ChainManager
from qrl.core.GenesisBlock import GenesisBlock
from qrl.core.State import State
from qrl.core.TransactionInfo import TransactionInfo
from qrl.core.txs.TransferTransaction import TransferTransaction
from qrl.core.misc import logger
from qrl.core.node import SyncState, POW
from qrl.core.p2p.p2pfactory import P2PFactory
from qrl.core.qrlnode import QRLNode
from qrl.crypto.misc import sha256
from qrl.generated import qrl_pb2
from qrl.services.PublicAPIService import PublicAPIService
from qrl.generated import qrl_pb2_grpc, qrl_pb2
from google.protobuf.json_format import MessageToJson
from google.protobuf.json_format import MessageToDict
import grpc
import time
import re
import simplejson as json
from os import environ
import logging
from datetime import datetime
from webargs import fields, validate
from webargs.flaskparser import use_kwargs, parser, use_args
import os

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
api = Api(app, version='1.0', title='Zeus-proxy advanced', 
          description='Zeus-proxy advanced & Testnet faucet')

ns = api.namespace('', description='Communicate with QRL Nodes')
fh = logging.FileHandler("v1.log")
ns.logger.addHandler(fh)

mainnet_node_public_address = "mainnet-1.automated.theqrl.org:19009"
testnet_node_public_address = "testnet-1.automated.theqrl.org:19009"
dt_string = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
CONNECTION_TIMEOUT = 5

HEX_SEED = environ.get('HEX_SEED')

def valid_qaddress(request_form):
    if len(request_form) == 79:
        return True
    else:
        return False

def tx_unbase64(tx_json_str):
    tx_json = json.loads(tx_json_str)
    tx_json["publicKey"] = base64tohex(tx_json["publicKey"])
    tx_json["signature"] = base64tohex(tx_json["signature"])
    tx_json["transactionHash"] = base64tohex(tx_json["transactionHash"])
    tx_json["transfer"]["addrsTo"] = [base64tohex(v) for v in tx_json["transfer"]["addrsTo"]]
    print(tx_json)
    return json.dumps(tx_json, indent=True, sort_keys=True)

def base64tohex(data):
    return hexlify(a2b_base64(data))

def send_testnet_coins(addrs_to, amount, ots_key):
    print(addrs_to)
    master_addr = None
    bytes_addrs_to = []
    fee = 1000000000
    message_data = None
    xmss_pk = XMSS.from_extended_seed(hstr2bin("010400bc2f62ec1161bba8eece8b511ce6e38f73254f42f045c08f403b9fb3f101a46d0f2b8d1b6da30c0ad8dd88b356e9022b")).pk
    # if len([addrs_to]) > 1:
    #     for i in addrs_to:
    #         bytes_addrs_to.append(bytes(hstr2bin(i)))
    # elif len([addrs_to]) == 1:
    #     bytes_addrs_to.append(bytes(hstr2bin(addrs_to[1:])))

    shor_amounts = [int(float(str(i) + "e9")) for i in [amount]]
    # Q0104008eeaa68d90419bc8401b15882d0bcf6c9190d652923f768cac621b58d7e7c7945d7fc97f
    tx = TransferTransaction.create(addrs_to=[bytes(hstr2bin(str(addrs_to[1:])))],
                                        amounts = shor_amounts,
                                        message_data = message_data,
                                        fee = fee,
                                        xmss_pk= xmss_pk,
                                        master_addr=master_addr)

        # Sign transaction
    src_xmss = XMSS.from_extended_seed(hstr2bin(HEX_SEED))
    src_xmss.set_ots_index(int(ots_key))
    tx.sign(src_xmss)

        # Print result
    txjson = tx_unbase64(tx.to_json())
    print(txjson)

    if not tx.validate():
        print("It was not possible to validate the signature")
        quit(1)

    print("\nTransaction Blob (signed): \n")
    txblob = tx.pbdata.SerializeToString()
    txblobhex = hexlify(txblob).decode()
    print(txblobhex)

    # Push transaction
    print("Sending to a QRL Node...")
    channel = grpc.insecure_channel(testnet_node_public_address)
    stub = qrl_pb2_grpc.PublicAPIStub(channel)
    push_transaction_req = qrl_pb2.PushTransactionReq(transaction_signed=tx.pbdata)
    push_transaction_resp = stub.PushTransaction(push_transaction_req, timeout=CONNECTION_TIMEOUT)

    return f'{push_transaction_resp}'

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),'favicon.ico',mimetype='image/vnd.microsoft.icon')

parser = api.parser()
parser.add_argument('qaddress', type=str, required=True)
parser.add_argument('amount', type=int, required=True)
parser.add_argument('ots_key', type=int, required=True)


@ns.route('/api/faucet')
@api.doc(description=
"""
# Attention: 
Return it to Q0104001f0b2c80a2b3843bc33e96c541801989d6a5dcbc17f0672de58853be808fcd24deed3470 once you're finished with the testnet coins!
""")
class Faucet(Resource):
    @api.expect(parser, validate=True)
    def post(self):
        args = parser.parse_args()
        if valid_qaddress(args['qaddress']):
            return send_testnet_coins(args['qaddress'], args['amount'], args['ots_key'])
        else:
            error = 'Invalid QRL address'
            return error

@ns.route('/grpc/mainnet/GetHeight')
class GetHeight_Mainnet(Resource):
    def get(self):
        channel = grpc.insecure_channel(mainnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetHeightReq()
        response = stub.GetHeight(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetHeight mainnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")

@ns.route('/grpc/testnet/GetHeight')
class GetHeight_Testnet(Resource):
    def get(self):
        channel = grpc.insecure_channel(testnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetHeightReq()
        response = stub.GetHeight(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetHeight testnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")

@ns.route('/grpc/mainnet/GetStats')
class GetStats_Mainnet(Resource):
    def get(self):
        channel = grpc.insecure_channel(mainnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetStatsReq()
        response = stub.GetStats(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetStats mainnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")


@ns.route('/grpc/testnet/GetStats')
class GetStats_Testnet(Resource):
    def get(self):
        channel = grpc.insecure_channel(testnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetStatsReq()
        response = stub.GetStats(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetStats testnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")


@ns.route('/grpc/mainnet/GetBalance/<string:qaddress>')
class GetBalance_Mainnet(Resource):
    def get(self, qaddress):
        binary_qrl_address = bytes(hstr2bin(qaddress[1:]))
        channel = grpc.insecure_channel(mainnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetBalanceReq(address=binary_qrl_address)
        response = stub.GetBalance(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetBalance mainnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")

@ns.route('/grpc/testnet/GetBalance/<string:qaddress>')
class GetBalance_Testnet(Resource):
    def get(self, qaddress):
        binary_qrl_address = bytes(hstr2bin(qaddress[1:]))
        channel = grpc.insecure_channel(testnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetBalanceReq(address=binary_qrl_address)
        response = stub.GetBalance(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetBalance testnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")

@ns.route('/grpc/mainnet/GetBlockByNumber/<number>')
class GetBlockByNumber_Mainnet(Resource):
    def get(self, number):
        channel = grpc.insecure_channel(mainnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetBlockByNumberReq(block_number=int(number))
        response = stub.GetBlockByNumber(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetBlockByNumber mainnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")



@ns.route('/grpc/testnet/GetBlockByNumber/<number>')
class GetBlockByNumber_Testnet(Resource):
    def get(self, number):
        channel = grpc.insecure_channel(mainnet_node_public_address)
        stub = qrl_pb2_grpc.PublicAPIStub(channel)
        node_request = qrl_pb2.GetBlockByNumberReq(block_number=int(number))
        response = stub.GetBlockByNumber(node_request, timeout=CONNECTION_TIMEOUT)
        dict_obj = MessageToDict(response)
        ns.logger.info(dt_string + " | GetBlockByNumber testnet 200 | " + request.environ.get("HTTP_X_FORWARDED_FOR", request.remote_addr))
        return app.response_class(json.dumps(dict_obj), mimetype="application/json")

if __name__ == '__main__':
    app.run(debug=True)
