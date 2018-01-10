# coding=utf-8
"""
This module implements all networking structures required for OPC UA secure conversation communication.
For SecureConversation messages refer to secureConversation.py

If all OPC UA basic data types are needed load the uaTypes module
"""
from scapy.contrib.opcua.helpers import UaTypePacket, UaPacketField
from scapy.contrib.opcua.binary.builtinTypes import UaBytesField, UaByteField, UaUInt32Field, UaByteString, \
    UaExpandedNodeId, UaNodeId
from scapy.contrib.opcua.binary.tcp import UaTcp
from scapy.contrib.opcua.binary.schemaTypes import UaOpenSecureChannelRequest, UaCloseSecureChannelRequest, \
    UaCloseSecureChannelResponse, nodeIdMappings


class UaSecureConversationMessageHeader(UaTypePacket):
    fields_desc = [UaBytesField("MessageType", None, 3),
                   UaByteField("IsFinal", b'F', displayAsChar=True),
                   UaUInt32Field("MessageSize", None),
                   UaUInt32Field("SecureChannelId", 0)]


class UaSecureConversationMessageFooter(UaTypePacket):
    fields_desc = []


class UaAsymmetricAlgorithmSecurityHeader(UaTypePacket):
    fields_desc = [UaPacketField("SecurityPolicyUri",
                                 UaByteString(length=None, data=b'http://opcfoundation.org/UA/SecurityPolicy#None'),
                                 UaByteString),
                   UaPacketField("SenderCertificate", UaByteString(), UaByteString),
                   UaPacketField("ReceiverCertificateThumbprint", UaByteString(), UaByteString)]

    def post_build(self, pkt, pay):
        if self.securityPolicy is not None:
            policyField, policy = self.getfield_and_val("SecurityPolicyUri")
            removeTo = len(policy)
            policy = bytes(UaByteString(data=self.securityPolicy.URI))
            return policy + pkt[removeTo:] + pay

        return pkt + pay


class UaSymmetricAlgorithmSecurityHeader(UaTypePacket):
    fields_desc = [UaUInt32Field("TokenId", 0)]


class UaSequenceHeader(UaTypePacket):
    fields_desc = [UaUInt32Field("SequenceNumber", 0),
                   UaUInt32Field("RequestId", 0)]


class MessageDispatcher(object):
    pass


class UaMessage(UaTypePacket):
    fields_desc = [UaPacketField("DataTypeEncoding", UaNodeId(), UaNodeId),
                   UaPacketField("Message", None, UaTypePacket)]

    _cache = {}

    @classmethod
    def dispatch_hook(cls, _pkt=None, *args, **kwargs):
        if _pkt is not None:
            nodeId = UaExpandedNodeId(_pkt)

            if nodeId.Identifier in nodeIdMappings:
                if nodeId.Identifier not in UaMessage._cache:
                    dispatchedClass = nodeIdMappings[nodeId.Identifier]
                    fields_desc = [UaPacketField("DataTypeEncoding", UaNodeId(), UaNodeId),
                                   UaPacketField("Message", dispatchedClass(), dispatchedClass)]
                    newDict = dict(cls.__dict__)
                    newDict["fields_desc"] = fields_desc
                    UaMessage._cache[nodeId.Identifier] = type(cls.__name__, cls.__bases__, newDict)
                return UaMessage._cache[nodeId.Identifier]

        return cls

    def post_build(self, pkt, pay):
        identifierField, identifier = self.DataTypeEncoding.getfield_and_val("Identifier")
        removeUpTo = len(self.DataTypeEncoding)

        if identifier is None:
            if self.Message is not None and self.Message.binaryEncodingId is not None:
                identifier = self.Message.binaryEncodingId
                encoding = self.DataTypeEncoding.getfieldval("Encoding")
                namespace = self.DataTypeEncoding.getfieldval("Namespace")

                pkt = UaNodeId(Encoding=encoding, Namespace=namespace, Identifier=identifier).build() + pkt[removeUpTo:]

        return pkt + pay


class UaSecureConversationAsymmetric(UaTcp):
    fields_desc = [
        UaPacketField("MessageHeader", UaSecureConversationMessageHeader(), UaSecureConversationMessageHeader),
        UaPacketField("SecurityHeader",
                      UaAsymmetricAlgorithmSecurityHeader(),
                      UaAsymmetricAlgorithmSecurityHeader),
        UaPacketField("SequenceHeader", UaSequenceHeader(), UaSequenceHeader),
        UaPacketField("Payload", UaMessage(Message=UaOpenSecureChannelRequest()), UaMessage),
        UaPacketField("MessageFooter", UaSecureConversationMessageFooter(), UaSecureConversationMessageFooter)]

    message_types = [b'OPN']

    def post_build(self, pkt, pay):
        messageTypeField, messageType = self.MessageHeader.getfield_and_val("MessageType")
        messageSizeField, messageSize = self.MessageHeader.getfield_and_val("MessageSize")

        typeBinary = pkt[:messageTypeField.sz]
        restString = pkt[messageTypeField.sz:]

        if messageType is None:
            if isinstance(self.SecurityHeader, UaAsymmetricAlgorithmSecurityHeader):
                typeBinary = b'OPN'
            elif isinstance(self.SecurityHeader, UaSymmetricAlgorithmSecurityHeader):
                if isinstance(self.Payload.Message, (UaCloseSecureChannelRequest, UaCloseSecureChannelResponse)):
                    typeBinary = B'CLO'
                else:
                    typeBinary = b'MSG'
            else:
                typeBinary = b'\x00\x00\x00'

        completePkt = typeBinary + restString + pay

        # if we are using a security policy always encrypt, because we exchange keys for signing as well
        if self.securityPolicy is not None:
            unencryptedSize = len(self.MessageHeader) + len(self.SecurityHeader)
            dataToEncrypt = completePkt[unencryptedSize:]
            dataToEncryptSize = len(dataToEncrypt)
            padding = self.securityPolicy.asymmetric_cryptography.padding(dataToEncryptSize)
            completePkt += padding
            completePkt += self.securityPolicy.asymmetric_cryptography.signature(completePkt)
            dataToEncrypt = completePkt[unencryptedSize:]
            encrypted = self.securityPolicy.asymmetric_cryptography.encrypt(dataToEncrypt)
            completePkt = completePkt[:unencryptedSize] + encrypted

        if messageSize is None:
            messageSize = len(completePkt)
            return messageSizeField.addfield(self,
                                             completePkt[:len(self.MessageHeader)][:-2 * messageSizeField.sz],
                                             messageSize) + completePkt[len(self.MessageHeader) - messageSizeField.sz:]

        return pkt + pay

    def pre_dissect(self, s):
        # TODO: Implement decryption of asymmetric messages?
        return s

    @classmethod
    def dispatch_hook(cls, _pkt=None, *args, **kwargs):
        return super(UaSecureConversationAsymmetric, cls).dispatch_hook(_pkt, args, kwargs)


class UaSecureConversationSymmetric(UaSecureConversationAsymmetric):
    fields_desc = [
        UaPacketField("MessageHeader", UaSecureConversationMessageHeader(), UaSecureConversationMessageHeader),
        UaPacketField("SecurityHeader",
                      UaSymmetricAlgorithmSecurityHeader(),
                      UaSymmetricAlgorithmSecurityHeader),
        UaPacketField("SequenceHeader", UaSequenceHeader(), UaSequenceHeader),
        UaPacketField("Payload", UaMessage(), UaMessage),
        UaPacketField("MessageFooter", UaSecureConversationMessageFooter(), UaSecureConversationMessageFooter)]

    message_types = [b'MSG', b'CLO']

    @classmethod
    def dispatch_hook(cls, _pkt=None, *args, **kwargs):
        return super(UaSecureConversationSymmetric, cls).dispatch_hook(_pkt, args, kwargs)
