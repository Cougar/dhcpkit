"""
Classes and constants for the message types defined in RFC 3315
"""

from ipaddress import IPv6Address

from dhcpkit.ipv6 import message_registry
from dhcpkit.protocol_element import ProtocolElement

MSG_SOLICIT = 1
MSG_ADVERTISE = 2
MSG_REQUEST = 3
MSG_CONFIRM = 4
MSG_RENEW = 5
MSG_REBIND = 6
MSG_REPLY = 7
MSG_RELEASE = 8
MSG_DECLINE = 9
MSG_RECONFIGURE = 10
MSG_INFORMATION_REQUEST = 11
MSG_RELAY_FORW = 12
MSG_RELAY_REPL = 13


# This subclass remains abstract
# noinspection PyAbstractClass
class Message(ProtocolElement):
    """
    The base class for DHCP messages.

    :type message_type: int
    :type from_client_to_server: bool
    :type from_server_to_client: bool
    """
    # These needs to be overwritten in subclasses
    message_type = 0
    from_client_to_server = False
    from_server_to_client = False

    @classmethod
    def determine_class(cls, buffer: bytes, offset: int=0) -> type:
        """
        Return the appropriate subclass from the registry, or UnknownClientServerMessage if no subclass is registered.

        :param buffer: The buffer to read data from
        :param offset: The offset in the buffer where to start reading
        :return: The best known class for this message data
        """
        message_type = buffer[offset]
        return message_registry.registry.get(message_type, UnknownMessage)


class UnknownMessage(Message):
    """
    Container for raw message content for cases where we don't know how to decode the message.

    :type message_data: bytes
    """

    def __init__(self, message_type: int, message_data: bytes=b''):
        super().__init__()
        self.message_type = message_type
        self.message_data = message_data

    # noinspection PyDocstring
    def validate(self):
        # Check if the data is bytes
        if not isinstance(self.message_type, int) or not (0 <= self.message_type < 2 ** 8):
            raise ValueError("Message type must be an unsigned 8-bit integer")

        # Check if the data is bytes
        if not isinstance(self.message_data, bytes):
            raise ValueError("Message data must consist of bytes")

    # noinspection PyDocstring
    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset = 0

        # Message always begin with a message type
        self.message_type = buffer[offset + my_offset]
        my_offset += 1

        message_data_len = length - my_offset
        self.message_data = buffer[offset + my_offset:offset + my_offset + message_data_len]
        my_offset += message_data_len

        self.validate()

        return my_offset

    # noinspection PyDocstring
    def save(self) -> bytes:
        self.validate()

        buffer = bytearray()
        buffer.append(self.message_type)
        buffer.extend(self.message_data)
        return buffer


class ClientServerMessage(Message):
    """
    https://tools.ietf.org/html/rfc3315#section-6

    All DHCP messages sent between clients and servers share an identical
    fixed format header and a variable format area for options.

    All values in the message header and in options are in network byte
    order.

    Options are stored serially in the options field, with no padding
    between the options.  Options are byte-aligned but are not aligned in
    any other way such as on 2 or 4 byte boundaries.

    The following diagram illustrates the format of DHCP messages sent
    between clients and servers::

       0                   1                   2                   3
       0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
      |    msg-type   |               transaction-id                  |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
      |                                                               |
      .                            options                            .
      .                           (variable)                          .
      |                                                               |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

      msg-type             Identifies the DHCP message type; the
                           available message types are listed in
                           section 5.3.

      transaction-id       The transaction ID for this message exchange.

      options              Options carried in this message; options are
                           described in section 22.

    :type transaction_id: bytes
    :type options: list[Option]
    """

    def __init__(self, transaction_id: bytes=b'\x00\x00\x00', options: []=None):
        super().__init__()
        self.transaction_id = transaction_id
        self.options = options or []

    # noinspection PyDocstring
    def validate(self):
        # Check if the transaction is 3 bytes
        if not isinstance(self.transaction_id, bytes) or len(self.transaction_id) != 3:
            raise ValueError("Transaction-id must be 3 bytes")

        # Check if all options are allowed
        self.validate_contains(self.options)
        for option in self.options:
            option.validate()

        # Make sure that all IAIDs are unique for their type
        iaids = {}
        for option in self.options:
            iaid = getattr(option, 'iaid', None)
            if iaid:
                option_class = self.get_element_class(option)
                existing = iaids.setdefault(option_class, [])
                if iaid in existing:
                    raise ValueError("IAID {} of {} is not unique".format(iaid, option_class.__name__))
                existing.append(iaid)

    def get_options_of_type(self, klass: type) -> list:
        """
        Get all options that are subclasses of the given class.

        :param klass: The class to look for
        :returns: The list of options

        :type klass: T
        :rtype: list[T()]
        """
        return [option for option in self.options if isinstance(option, klass)]

    def get_option_of_type(self, klass: type) -> object or None:
        """
        Get the first option that is a subclass of the given class.

        :param klass: The class to look for
        :returns: The option or None

        :type klass: T
        :rtype: T() or None
        """
        for option in self.options:
            if isinstance(option, klass):
                return option

    # noinspection PyDocstring
    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset = 0

        # These message types always begin with a message type and a transaction id
        message_type = buffer[offset + my_offset]
        my_offset += 1

        if message_type != self.message_type:
            raise ValueError('The provided buffer does not contain {} data'.format(self.__class__.__name__))

        self.transaction_id = buffer[offset + my_offset:offset + my_offset + 3]
        my_offset += 3

        # Parse the options
        from dhcpkit.ipv6.options import Option

        max_length = length or (len(buffer) - offset)
        while max_length > my_offset:
            used_buffer, option = Option.parse(buffer, offset=offset + my_offset)

            self.options.append(option)
            my_offset += used_buffer

        self.validate()

        return my_offset

    # noinspection PyDocstring
    def save(self) -> bytes:
        self.validate()

        buffer = bytearray()
        buffer.append(self.message_type)
        buffer.extend(self.transaction_id)
        for option in self.options:
            buffer.extend(option.save())
        return buffer


class RelayServerMessage(Message):
    """
    https://tools.ietf.org/html/rfc3315#section-7

    Relay agents exchange messages with servers to relay messages between
    clients and servers that are not connected to the same link.

    All values in the message header and in options are in network byte
    order.

    Options are stored serially in the options field, with no padding
    between the options.  Options are byte-aligned but are not aligned in
    any other way such as on 2 or 4 byte boundaries.

    There are two relay agent messages, which share the following format::

       0                   1                   2                   3
       0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
      |    msg-type   |   hop-count   |                               |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+                               |
      |                                                               |
      |                         link-address                          |
      |                                                               |
      |                               +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-|
      |                               |                               |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+                               |
      |                                                               |
      |                         peer-address                          |
      |                                                               |
      |                               +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-|
      |                               |                               |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+                               |
      .                                                               .
      .            options (variable number and length)   ....        .
      |                                                               |
      +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

    :type hop_count: int
    :type link_address: IPv6Address
    :type peer_address: IPv6Address
    :type options: list[Option]
    """

    def __init__(self, hop_count: int=0, link_address: IPv6Address=None, peer_address: IPv6Address=None,
                 options: []=None):
        super().__init__()
        self.hop_count = hop_count
        self.link_address = link_address
        self.peer_address = peer_address
        self.options = options or []

    # noinspection PyDocstring
    def validate(self):
        # Check hop-count
        if not isinstance(self.hop_count, int) or not (0 <= self.hop_count < 2 ** 8):
            raise ValueError("Hop-count must be an unsigned 8 bit integer")

        if not isinstance(self.link_address, IPv6Address) or self.link_address.is_multicast:
            raise ValueError("Link-address must be a non-multicast IPv6 address")

        if not isinstance(self.peer_address, IPv6Address) or self.peer_address.is_multicast:
            raise ValueError("Peer-address must be a non-multicast IPv6 address")

        # Check if all options are allowed
        self.validate_contains(self.options)
        for option in self.options:
            option.validate()

    def get_options_of_type(self, klass: type) -> list:
        """
        Get all options that are subclasses of the given class.

        :param klass: The class to look for
        :returns: The list of options

        :type klass: T
        :rtype: list[T()]
        """
        return [option for option in self.options if isinstance(option, klass)]

    def get_option_of_type(self, klass) -> object or None:
        """
        Get the first option that is a subclass of the given class.

        :param klass: The class to look for
        :type klass: T
        :returns: The option or None
        :rtype: T()
        """
        for option in self.options:
            if isinstance(option, klass):
                return option

    @property
    def relayed_message(self) -> Message or None:
        """
        Utility method to easily get the relayed message from the RelayMessageOption inside this RelayServerMessage.

        :return: The message, if found
        """
        from dhcpkit.ipv6.options import RelayMessageOption

        for option in self.options:
            if isinstance(option, RelayMessageOption):
                return option.relayed_message

        # No embedded message found
        return None

    # noinspection PyDocstring
    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        my_offset = 0

        # These message types always begin with a message type, a hop count, the link address and the peer address
        self.message_type = buffer[offset + my_offset]
        my_offset += 1

        self.hop_count = buffer[offset + my_offset]
        my_offset += 1

        self.link_address = IPv6Address(buffer[offset + my_offset:offset + my_offset + 16])
        my_offset += 16

        self.peer_address = IPv6Address(buffer[offset + my_offset:offset + my_offset + 16])
        my_offset += 16

        # Parse the options
        from dhcpkit.ipv6.options import Option

        max_length = length or (len(buffer) - offset)
        while max_length > my_offset:
            used_buffer, option = Option.parse(buffer, offset=offset + my_offset)
            self.options.append(option)
            my_offset += used_buffer

        self.validate()

        return my_offset

    # noinspection PyDocstring
    def save(self) -> bytes:
        self.validate()

        buffer = bytearray()
        buffer.append(self.message_type)
        buffer.append(self.hop_count)
        buffer.extend(self.link_address.packed)
        buffer.extend(self.peer_address.packed)
        for option in self.options:
            buffer.extend(option.save())
        return buffer


class SolicitMessage(ClientServerMessage):
    """
    SOLICIT (1)
        A client sends a Solicit message to locate servers.
    """
    message_type = MSG_SOLICIT
    from_client_to_server = True


class AdvertiseMessage(ClientServerMessage):
    """
    A server sends an Advertise message to indicate that it is available for DHCP service, in response to a
    Solicit message received from a client.
    """
    message_type = MSG_ADVERTISE
    from_server_to_client = True


class RequestMessage(ClientServerMessage):
    """
    A client sends a Request message to request configuration parameters, including IP addresses, from a
    specific server.
    """
    message_type = MSG_REQUEST
    from_client_to_server = True


class ConfirmMessage(ClientServerMessage):
    """
    A client sends a Confirm message to any available server to determine whether the addresses it was assigned
    are still appropriate to the link to which the client is connected.
    """
    message_type = MSG_CONFIRM
    from_client_to_server = True


class RenewMessage(ClientServerMessage):
    """
    A client sends a Renew message to the server that originally provided the client's addresses and configuration
    parameters to extend the lifetimes on the addresses assigned to the client and to update other configuration
    parameters.
    """
    message_type = MSG_RENEW
    from_client_to_server = True


class RebindMessage(ClientServerMessage):
    """
    A client sends a Rebind message to any available server to extend the lifetimes on the addresses assigned to
    the client and to update other configuration parameters; this message is sent after a client receives no
    response to a Renew message.
    """
    message_type = MSG_REBIND
    from_client_to_server = True


class ReplyMessage(ClientServerMessage):
    """
    A server sends a Reply message containing assigned addresses and configuration parameters in response to a
    Solicit, Request, Renew, Rebind message received from a client.  A server sends a Reply message containing
    configuration parameters in response to an Information-request message.  A server sends a Reply message in
    response to a Confirm message confirming or denying that the addresses assigned to the client are appropriate
    to the link to which the client is connected.  A server sends a Reply message to acknowledge receipt of a
    Release or Decline message.
    """
    message_type = MSG_REPLY
    from_server_to_client = True


class ReleaseMessage(ClientServerMessage):
    """
    A client sends a Release message to the server that assigned addresses to the client to indicate that the
    client will no longer use one or more of the assigned addresses.
    """
    message_type = MSG_RELEASE
    from_client_to_server = True


class DeclineMessage(ClientServerMessage):
    """
    A client sends a Decline message to a server to indicate that the client has determined that one or more
    addresses assigned by the server are already in use on the link to which the client is connected.
    """
    message_type = MSG_DECLINE
    from_client_to_server = True


class ReconfigureMessage(ClientServerMessage):
    """
    A server sends a Reconfigure message to a client to inform the client that the server has new or updated
    configuration parameters, and that the client is to initiate a Renew/Reply or Information-request/Reply
    transaction with the server in order to receive the updated information.
    """
    message_type = MSG_RECONFIGURE
    from_server_to_client = True


class InformationRequestMessage(ClientServerMessage):
    """
    A client sends an Information-request message to a server to request configuration parameters without the
    assignment of any IP addresses to the client.
    """
    message_type = MSG_INFORMATION_REQUEST
    from_client_to_server = True


class RelayForwardMessage(RelayServerMessage):
    """
    A relay agent sends a Relay-forward message to relay messages to servers, either directly or through another
    relay agent.  The received message, either a client message or a Relay-forward message from another relay
    agent, is encapsulated in an option in the Relay-forward message.
    """
    message_type = MSG_RELAY_FORW
    from_client_to_server = True

    def wrap_response(self, response: ClientServerMessage) -> Message:
        """
        The incoming message was wrapped in this RelayForwardMessage. Let this RelayForwardMessage then create a
        RelayReplyMessage with the correct options and wrap the reply .

        :param response: The response that is going to be sent to the client
        :return: The RelayReplyMessage wrapping the response
        :rtype: RelayReplyMessage
        """
        from dhcpkit.ipv6.options import RelayMessageOption

        my_response = RelayReplyMessage(self.hop_count, self.link_address, self.peer_address)

        for option in self.options:
            if option.echo_to_relay:
                # Echo back options that want to be echoed
                my_response.options.append(option)

            elif isinstance(option, RelayMessageOption):
                # Check what we contained in our RelayMessageOption
                relayed_message = option.relayed_message
                if isinstance(relayed_message, RelayForwardMessage):
                    # Our relayed message is another relay message: let it wrap the response too
                    my_response.options.append(RelayMessageOption(
                        relayed_message=relayed_message.wrap_response(response)
                    ))
                elif isinstance(option.relayed_message, ClientServerMessage):
                    # Our relayed message is a ClientServerMessage, so place the response here in the RelayReplyMessage
                    my_response.options.append(RelayMessageOption(
                        relayed_message=response
                    ))
                else:
                    raise ValueError("RelayForwardMessages can only contain "
                                     "other RelayForwardMessages and ClientServerMessages")

        return my_response


class RelayReplyMessage(RelayServerMessage):
    """
    A server sends a Relay-reply message to a relay agent containing a message that the relay agent delivers to a
    client.  The Relay-reply message may be relayed by other relay agents for delivery to the destination relay
    agent.

    The server encapsulates the client message as an option in the Relay-reply message, which the relay agent
    extracts and relays to the client.
    """
    message_type = MSG_RELAY_REPL
    from_server_to_client = True

# Register the classes in this file
message_registry.register(SolicitMessage)
message_registry.register(AdvertiseMessage)
message_registry.register(RequestMessage)
message_registry.register(ConfirmMessage)
message_registry.register(RenewMessage)
message_registry.register(RebindMessage)
message_registry.register(ReplyMessage)
message_registry.register(ReleaseMessage)
message_registry.register(DeclineMessage)
message_registry.register(ReconfigureMessage)
message_registry.register(InformationRequestMessage)
message_registry.register(RelayForwardMessage)
message_registry.register(RelayReplyMessage)