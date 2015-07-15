from abc import abstractmethod, ABCMeta
from collections import ChainMap
from inspect import Parameter
import inspect
import collections

infinite = 2 ** 31 - 1


class AutoMayContainTree(ABCMeta):
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # Get all the ChainMaps from the parents
        parent_may_contains = [getattr(base, '_may_contain') for base in bases
                               if isinstance(getattr(base, '_may_contain', None), ChainMap)]

        # And create our local one with those as lookup targets
        cls._may_contain = ChainMap({}, *parent_may_contains)

        return cls


class StructuredElement(metaclass=AutoMayContainTree):
    """
    A StructuredElement is a specific kind of class that represents a protocol message or option. Structured elements
    have the following extra requirements:

    - The constructor parameters and the internal state properties must be identical
      So if an object has a property `timeout` which is an integer then the constructor must accept a named parameter
      called `timeout` which is stored in that property. The constructor must have appropriate default values if
      possible. Empty objects, lists, dictionaries etc are represented by a default value of None.
    - The full internal state of the object must be loadable from a bytes object with the load_from method
    - The full internal state of the object must be storable as a bytes object with the save method
    """

    # This will be set by the meta-class
    _may_contain = None

    def validate(self):
        pass

    def validate_contains(self, elements):
        # Count occurrence
        occurrence_counters = collections.Counter()
        for element in elements:
            element_class = self.get_element_class(element)
            if element_class is None:
                raise ValueError("{} cannot contain {}".format(self.__class__.__name__, element.__class__.__name__))

            # Count its occurrence
            occurrence_counters[element_class] += 1

        # Check max occurrence
        for element_class, (min_occurrence, max_occurrence) in self._may_contain.items():
            count = occurrence_counters[element_class]
            if count > max_occurrence:
                if max_occurrence == 1:
                    raise ValueError("{} may only contain 1 {}".format(self.__class__.__name__, element_class.__name__))
                else:
                    raise ValueError("{} may only contain {} {}s".format(self.__class__.__name__, max_occurrence,
                                                                         element_class.__name__))
            elif count < min_occurrence:
                if min_occurrence == 1:
                    raise ValueError("{} must contain at least 1 {}".format(self.__class__.__name__,
                                                                            element_class.__name__))
                else:
                    raise ValueError("{} must contain at least {} {}s".format(self.__class__.__name__, max_occurrence,
                                                                              element_class.__name__))

    @classmethod
    @abstractmethod
    def determine_class(cls, buffer: bytes, offset: int=0) -> type:
        """
        Return the appropriate class to parse this element with.

        :param buffer: The buffer to read data from
        :param offset: The offset in the buffer where to start reading
        :return: The best known class for this data
        """
        pass

    @classmethod
    def parse(cls, buffer: bytes, offset: int=0, length: int=None) -> (int, type):
        """
        Constructor for a new element of which the state is automatically loaded from the given buffer. Both the number
        of bytes used from the buffer and the instantiated element are returned. The class of the returned element may
        be a subclass of the current class if the parser can determine that the data in the buffer contains a subtype.

        :param buffer: The buffer to read data from
        :param offset: The offset in the buffer where to start reading
        :param length: The amount of data we are allowed to read from the buffer
        :return: The number of bytes used from the buffer and the resulting element
        """
        element_class = cls.determine_class(buffer, offset=offset)
        element = element_class()
        length = element.load_from(buffer, offset=offset, length=length)
        return length, element

    @abstractmethod
    def load_from(self, buffer: bytes, offset: int=0, length: int=None) -> int:
        """
        Load the internal state of this object from the given buffer. The buffer may contain more data after the
        structured element is parsed. This data is ignored.

        :param buffer: The buffer to read data from
        :param offset: The offset in the buffer where to start reading
        :param length: The amount of data we are allowed to read from the buffer
        :return: The number of bytes used from the buffer
        """
        return 0

    @abstractmethod
    def save(self) -> bytes:
        """
        Save the internal state of this object as a buffer.

        :return: The buffer with the data from this element
        """
        return b''

    def __eq__(self, other: object) -> bool:
        """
        Compare this object to another object. The result will be True if they are of the same class and if the
        properties have equal values and False otherwise.

        :param other: The other object
        :return: Whether this object is equal to the other one
        """
        # Use strict comparison, one being a subclass of the other is not good enough
        if type(self) is not type(other):
            return NotImplemented

        # Get the signature of the __init__ method to find the properties we need to compare
        # This is why the object properties and __init__ parameters need to match, besides it being good practice for
        # an object that represents a protocol element anyway...
        signature = inspect.signature(self.__init__)

        # Compare the discovered properties
        for parameter in signature.parameters.values():
            # Skip any potential *args and **kwargs in the method signature
            if parameter.kind in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD):
                continue

            if getattr(self, parameter.name) != getattr(other, parameter.name):
                return False

        # Amazing, all properties seem equal
        return True

    def __repr__(self):
        # Get the signature of the __init__ method to find the properties we need to compare
        # This is why the object properties and __init__ parameters need to match, besides it being good practice for
        # an object that represents a protocol element anyway...
        signature = inspect.signature(self.__init__)

        # Create a list of string with "parameter=value" for each parameter of __init__
        options_repr = ['{}={}'.format(parameter.name, repr(getattr(self, parameter.name)))
                        for parameter in signature.parameters.values()
                        if parameter.kind not in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)]

        # And construct a constructor call to show
        return '{}({})'.format(self.__class__.__name__, ', '.join(options_repr))

    def __str__(self):
        # Use the same strategy as __repr__ but do nice indenting etc.
        signature = inspect.signature(self.__init__)

        parameter_names = [parameter.name
                           for parameter in signature.parameters.values()
                           if parameter.kind not in (Parameter.VAR_POSITIONAL, Parameter.VAR_KEYWORD)]

        if len(parameter_names) == 0:
            # No parameters: inline
            return '{}()'.format(self.__class__.__name__)
        elif len(parameter_names) == 1:
            # One parameter: inline unless the parameter has a multi-line string output
            parameter_name = parameter_names[0]
            attr_value = getattr(self, parameter_name)
            lines = str(attr_value).split('\n')

            output = '{}('.format(self.__class__.__name__, parameter_name)

            if len(lines) == 1:
                output += '{}={}'.format(parameter_name, lines[0])
            else:
                output += '\n  {}='.format(parameter_name)
                output += '{}\n'.format(lines[0])
                for line in lines[1:-1]:
                    output += '  {}\n'.format(line)
                output += '  {}\n'.format(lines[-1])

            output += ')'
            return output

        # Multiple parameters are shown one parameter per line
        output = '{}(\n'.format(self.__class__.__name__)
        for parameter_name in parameter_names:
            attr_value = getattr(self, parameter_name)
            if attr_value and isinstance(attr_value, list):
                # Parameters containing lists show the list content indented
                output += '  {}=[\n'.format(parameter_name)
                for element in attr_value:
                    lines = str(element).split('\n')
                    for line in lines[:-1]:
                        output += '    {}\n'.format(line)
                    output += '    {},\n'.format(lines[-1])
                output += '  ],\n'
            else:
                # Multi-line content is shown indented
                output += '  {}='.format(parameter_name)
                lines = str(attr_value).split('\n')
                if len(lines) == 1:
                    output += '{},\n'.format(lines[0])
                else:
                    output += '{}\n'.format(lines[0])
                    for line in lines[1:-1]:
                        output += '  {}\n'.format(line)
                    output += '  {},\n'.format(lines[-1])

        output += ')'

        return output

    @classmethod
    def add_may_contain(cls, klass: type, min_occurrence: int=0, max_occurrence: int=infinite):
        # Make sure we have our own dictionary so we don't accidentally add to our parent's
        if '_may_contain' not in cls.__dict__:
            cls._may_contain = dict()

        # Add it
        cls._may_contain[klass] = (min_occurrence, max_occurrence)

    @classmethod
    def may_contain(cls, element) -> bool:
        return cls.get_element_class(element) is not None

    @classmethod
    def get_element_class(cls, element: object) -> type:
        """
        Get the class this element is classified as, for occurrence counting.

        :param element: Some element
        :return: The class it classifies as
        """
        # This class has its own list of what it may contain: check it
        for klass, (min_occurrence, max_occurrence) in cls._may_contain.items():
            if max_occurrence < 1:
                # May not contain this, stop looking
                return None

            if isinstance(element, klass):
                return klass
