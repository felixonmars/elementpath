# -*- coding: utf-8 -*-
#
# Copyright (c), 2018, SISSA (International School for Advanced Studies).
# All rights reserved.
# This file is distributed under the terms of the MIT License.
# See the file 'LICENSE' in the root directory of the present
# distribution, or http://opensource.org/licenses/MIT.
#
# @author Davide Brunato <brunato@sissa.it>
#
import sys
import decimal
import math
import codecs
import datetime
import time
import re
from itertools import product
from abc import ABCMeta
from collections import MutableSequence

from .compat import PY3, string_base_type, unicode_chr, urllib_quote, unicode_type, urlparse, URLError
from .exceptions import ElementPathNameError, ElementPathTypeError, ElementPathMissingContextError
from .datatypes import DateTime, Date, Time, Timezone, GregorianDay, GregorianMonth, GregorianMonthDay, \
    GregorianYear, GregorianYearMonth, UntypedAtomic, Duration, YearMonthDuration, DayTimeDuration
from .namespaces import (
    XPATH_FUNCTIONS_NAMESPACE, XPATH_2_DEFAULT_NAMESPACES, XSD_NOTATION, XSD_ANY_ATOMIC_TYPE,
    qname_to_prefixed, prefixed_to_qname, get_namespace
)
from .xpath_helpers import is_document_node, is_xpath_node, is_element_node, is_attribute_node, \
    node_name, node_string_value, node_nilled, node_base_uri, node_document_uri, boolean_value, \
    data_value, string_value
from .tdop_parser import create_tokenizer
from .xpath1_parser import XML_NCNAME_PATTERN, XPath1Parser
from .schema_proxy import AbstractSchemaProxy

###
# Regex compiled patterns for XSD constructors
WHITESPACES_RE_PATTERN = re.compile(r'\s+')
NMTOKEN_PATTERN = re.compile(r'^[\w.\-:]+$', flags=0 if PY3 else re.U)
NAME_PATTERN = re.compile(r'^(?:[^\d\W]|:)[\w.\-:]*$', flags=0 if PY3 else re.U)
NCNAME_PATTERN = re.compile(r'^[^\d\W][\w.\-]*$', flags=0 if PY3 else re.U)
QNAME_PATTERN = re.compile(
    r'^(?:(?P<prefix>[^\d\W][\w.-]*):)?(?P<local>[^\d\W][\w.-]*)$', flags=0 if PY3 else re.U
)
HEX_BINARY_PATTERN = re.compile(r'^[0-9a-fA-F]+$')
NOT_BASE64_BINARY_PATTERN = re.compile(r'[^0-9a-zA-z+/= \t\n]')
LANGUAGE_CODE_PATTERN = re.compile(r'^([a-zA-Z]{2}|[iI]-[a-zA-Z]+|[xX]-[a-zA-Z]{1,8})(-[a-zA-Z]{1,8})*$')
WRONG_ESCAPE_PATTERN = re.compile(r'%(?![a-eA-E\d]{2})')


def collapse_white_spaces(s):
    return WHITESPACES_RE_PATTERN.sub(' ', s).strip()


class XPath2Parser(XPath1Parser):
    """
    XPath 2.0 expression parser class. This is the default parser used by XPath selectors.
    A parser instance represents also the XPath static context. With *variables* you can pass
    a dictionary with the static context's in-scope variables.
    Provide a *namespaces* dictionary argument for mapping namespace prefixes to URI inside
    expressions. If *strict* is set to `False` the parser enables also the parsing of QNames,
    like the ElementPath library. There are some additional XPath 2.0 related arguments.

    :param namespaces: a dictionary with mapping from namespace prefixes into URIs.
    :param variables: a dictionary with the static context's in-scope variables.
    :param strict: if strict mode is `False` the parser enables parsing of QNames, \
    like the ElementPath library. Default is `True`.
    :param default_namespace: the default namespace to apply to unprefixed names. \
    For default no namespace is applied (empty namespace '').
    :param function_namespace: the default namespace to apply to unprefixed function names. \
    For default the namespace "http://www.w3.org/2005/xpath-functions" is used.
    :param schema: the schema proxy class or instance to use for types, attributes and elements lookups. \
    If an `AbstractSchemaProxy` subclass is provided then a schema proxy instance is built without the \
    optional argument, that involves a mapping of only XSD builtin types. If it's not provided the \
    XPath 2.0 \schema's related expressions cannot be used.
    :param compatibility_mode: if set to `True` the parser instance works with XPath 1.0 compatibility rules.
    """
    SYMBOLS = XPath1Parser.SYMBOLS | {
        'union', 'intersect', 'instance', 'castable', 'if', 'then', 'else', 'for', 'to',
        'some', 'every', 'in', 'satisfies', 'item', 'satisfies', 'cast', 'treat',
        'return', 'except', '?', 'as', 'of',

        # Comments
        '(:', ':)',

        # Value comparison operators
        'eq', 'ne', 'lt', 'le', 'gt', 'ge',
        
        # Node comparison operators
        'is', '<<', '>>',

        # Mathematical operators
        'idiv',

        # Node type functions
        'document-node', 'schema-attribute', 'element', 'schema-element', 'attribute', 'empty-sequence',

        # Accessor functions
        'node-name', 'nilled', 'data', 'base-uri', 'document-uri',

        # Number functions
        'abs', 'round-half-to-even',

        # String functions
        'codepoints-to-string', 'string-to-codepoints', 'compare', 'codepoint-equal',
        'string-join', 'normalize-unicode', 'upper-case', 'lower-case', 'encode-for-uri',
        'iri-to-uri', 'escape-html-uri', 'starts-with', 'ends-with',

        # General functions for sequences
        'distinct-values', 'empty', 'exists', 'index-of', 'insert-before', 'remove',
        'reverse', 'subsequence', 'unordered',

        # Cardinality functions for sequences
        'zero-or-one', 'one-or-more', 'exactly-one',

        # TODO: Pattern matching functions
        # 'matches', 'replace', 'tokenize',

        # Functions for extracting fragments from xs:duration
        'years-from-duration', 'months-from-duration', 'days-from-duration',
        'hours-from-duration', 'minutes-from-duration', 'seconds-from-duration',

        # Functions for extracting fragments from xs:dateTime
        'year-from-dateTime', 'month-from-dateTime', 'day-from-dateTime', 'hours-from-dateTime',
        'minutes-from-dateTime', 'seconds-from-dateTime', 'timezone-from-dateTime',

        # Functions for extracting fragments from xs:date
        'year-from-date', 'month-from-date', 'day-from-date', 'timezone-from-date',

        # Functions for extracting fragments from xs:time
        'hours-from-time', 'minutes-from-time', 'seconds-from-time', 'timezone-from-time',

        # Timezone adjustment functions
        'adjust-dateTime-to-timezone', 'adjust-date-to-timezone', 'adjust-time-to-timezone',

        # Functions Related to QNames (QName function is also a constructor)
        'QName', 'local-name-from-QName', 'prefix-from-QName', 'local-name-from-QName',
        'namespace-uri-from-QName', 'namespace-uri-for-prefix', 'in-scope-prefixes', 'resolve-QName',

        # Context functions
        'current-dateTime', 'current-date', 'current-time', 'implicit-timezone',

        # Node set functions
        'root',

        # Error function
        'error',

        # XSD builtins constructors ('string', 'boolean' and 'QName' already registered as functions)
        'string1', 'boolean1',
        'normalizedString', 'token', 'language', 'Name', 'NCName', 'ENTITY', 'ID', 'IDREF',
        'NMTOKEN', 'anyURI', 'decimal', 'int', 'integer', 'long', 'short', 'byte', 'double',
        'float', 'nonNegativeInteger', 'positiveInteger', 'nonPositiveInteger', 'negativeInteger',
        'unsignedLong', 'unsignedInt', 'unsignedShort', 'unsignedByte', 'dateTime', 'date', 'time',
        'gDay', 'gMonth', 'gYear', 'gMonthDay', 'gYearMonth', 'duration', 'dayTimeDuration',
        'yearMonthDuration', 'base64Binary', 'hexBinary'
    }

    QUALIFIED_FUNCTIONS = {
        'attribute', 'comment', 'document-node', 'element', 'empty-sequence', 'if', 'item', 'node',
        'processing-instruction', 'schema-attribute', 'schema-element', 'text', 'typeswitch'
    }

    DEFAULT_NAMESPACES = XPATH_2_DEFAULT_NAMESPACES

    def __init__(self, namespaces=None, variables=None, strict=True, default_namespace='',
                 function_namespace=None, schema=None, compatibility_mode=False):
        super(XPath2Parser, self).__init__(namespaces, variables, strict)
        if '' not in self.namespaces and default_namespace:
            self.namespaces[''] = default_namespace

        if function_namespace is None:
            self.function_namespace = XPATH_FUNCTIONS_NAMESPACE
        else:
            self.function_namespace = function_namespace

        if schema is None:
            self.schema = None
        elif not isinstance(schema, AbstractSchemaProxy):
            raise ElementPathTypeError("schema argument must be a subclass or instance of AbstractSchemaProxy!")
        else:
            self.schema = schema
            self.symbol_table = self.symbol_table.copy()
            for xsd_type in self.schema.iter_atomic_types():
                self.schema_constructor(xsd_type.name)
            self.tokenizer = create_tokenizer(self.symbol_table, XML_NCNAME_PATTERN)

        if compatibility_mode is False:
            self.compatibility_mode = False  # It's already a XPath1Parser class property

    @property
    def version(self):
        return '2.0'

    @property
    def default_namespace(self):
        return self.namespaces.get('')

    def advance(self, *symbols):
        super(XPath2Parser, self).advance(*symbols)
        if self.next_token.symbol == '(:':
            token = self.token
            if token is None:
                self.next_token.comment = self.comment().strip()
            elif token.comment is None:
                token.comment = self.comment().strip()
            else:
                token.comment = '%s %s' % (token.comment, self.comment().strip())
            super(XPath2Parser, self).advance()
        return self.next_token

    def comment(self):
        """
        Parses and consumes a XPath 2.0 comment. Comments are delimited by symbols
        '(:' and ':)' and can be nested. A comment is attached to the previous token
        or the next token when the previous is None.
        """
        if self.next_token.symbol != '(:':
            return

        comment_level = 1
        comment = []
        while comment_level:
            comment.append(self.raw_advance('(:', ':)'))
            next_token = self.next_token
            if next_token.symbol == ':)':
                comment_level -= 1
                if comment_level:
                    comment.append(str(next_token.value))
            elif next_token.symbol == '(:':
                comment_level += 1
                comment.append(str(next_token.value))
        return ''.join(comment)

    @classmethod
    def create_constructor(cls, symbol, bp=0):
        """Creates a constructor token class."""
        def nud_(self):
            self.parser.advance('(')
            if self.parser.next_token.symbol != ')':
                self[0:] = self.parser.expression(5),
            self.parser.advance(')')

            try:
                self.value = self.evaluate()  # Static context evaluation
            except ElementPathMissingContextError:
                self.value = None
            return self

        token_class_name = str("_%s_constructor_token" % symbol.replace(':', '_'))
        kwargs = {
            'symbol': symbol,
            'label': 'constructor',
            'pattern': r'\b%s(?=\s*\(|\s*\(\:.*\:\)\()' % symbol,
            'lbp': bp,
            'rbp': bp,
            'nud': nud_,
            '__module__': cls.__module__,
            '__qualname__': token_class_name,
            '__return__': None
        }
        token_class = ABCMeta(token_class_name, (cls.token_base_class,), kwargs)
        MutableSequence.register(token_class)
        return token_class

    @classmethod
    def constructor(cls, symbol, bp=90):
        """Registers a token class for an XSD builtin atomic type constructor function."""
        if symbol not in cls.SYMBOLS:
            raise ElementPathNameError('%r is not a symbol of the parser %r.' % (symbol, cls))
        token_class = cls.create_constructor(symbol, bp=bp)
        cls.symbol_table[symbol] = token_class
        setattr(sys.modules[cls.__module__], token_class.__name__, token_class)
        return token_class

    def schema_constructor(self, type_qname):
        """Registers a token class for a schema atomic type constructor function."""
        def evaluate_(self_, context=None):
            item = self_.get_argument(context)
            return [] if item is None else self_.parser.schema.cast_as(self_[0].evaluate(context), type_qname)

        if type_qname not in {XSD_ANY_ATOMIC_TYPE, XSD_NOTATION}:
            symbol = qname_to_prefixed(type_qname, self.namespaces)
            token_class = self.create_constructor(symbol, bp=90)
            token_class.evaluate = evaluate_
            self.symbol_table[symbol] = token_class
            return token_class
        elif type_qname == XSD_NOTATION:
            return

    def next_is_path_step_token(self):
        return self.next_token.label in ('axis', 'function') or self.next_token.symbol in {
            '(integer)', '(string)', '(float)',  '(decimal)', '(name)', '*', '@', '..', '.', '(', '/', '{'
        }

    def next_is_sequence_type_token(self):
        return self.next_token.symbol in {
            '(name)', ':', 'empty-sequence', 'item', 'document-node', 'element', 'attribute',
            'text', 'comment', 'processing-instruction', 'schema-attribute', 'schema-element'
        }


##
# XPath 2.0 definitions
register = XPath2Parser.register
unregister = XPath2Parser.unregister
literal = XPath2Parser.literal
prefix = XPath2Parser.prefix
infix = XPath2Parser.infix
infixr = XPath2Parser.infixr
method = XPath2Parser.method
constructor = XPath2Parser.constructor
function = XPath2Parser.function
axis = XPath2Parser.axis

##
# Remove symbols that have to be redefined for XPath 2.0.
unregister(',')

###
# Symbols
register('then')
register('else')
register('in')
register('return')
register('satisfies')
register('as')
register('of')
register('?')
register('(:')
register(':)')


###
# Node sequence composition
@method(infix('union', bp=50))
def select(self, context=None):
    if context is not None:
        results = {item for k in range(2) for item in self[k].select(context.copy())}
        for item in context.iter():
            if item in results:
                yield item


@method(infix('intersect', bp=55))
def select(self, context=None):
    if context is not None:
        results = set(self[0].select(context.copy())) & set(self[1].select(context.copy()))
        for item in context.iter():
            if item in results:
                yield item


@method(infix('except', bp=55))
def select(self, context=None):
    if context is not None:
        results = set(self[0].select(context.copy())) - set(self[1].select(context.copy()))
        for item in context.iter():
            if item in results:
                yield item


###
# 'if' expression
@method('if', bp=20)
def nud(self):
    self.parser.advance('(')
    self[:] = self.parser.expression(),
    self.parser.advance(')')
    self.parser.advance('then')
    self[1:] = self.parser.expression(),
    self.parser.advance('else')
    self[2:] = self.parser.expression(),
    return self


@method('if')
def evaluate(self, context=None):
    if boolean_value(self[0].evaluate(context)):
        return self[1].evaluate(context)
    else:
        return self[2].evaluate(context)


@method('if')
def select(self, context=None):
    if boolean_value(list(self[0].select(context))):
        for result in self[1].select(context):
            yield result
    else:
        for result in self[2].select(context):
            yield result


###
# Quantified expressions
@method('some', bp=20)
@method('every', bp=20)
def nud(self):
    del self[:]
    while True:
        self.parser.next_token.expected('$')
        self.append(self.parser.expression(5))
        self.parser.advance('in')
        self.append(self.parser.expression(5))
        if self.parser.next_token.symbol == ',':
            self.parser.advance()
        else:
            break

    self.parser.advance('satisfies')
    self.append(self.parser.expression(5))
    return self


@method('some')
@method('every')
def evaluate(self, context=None):
    if context is None:
        return

    some = self.symbol == 'some'
    selectors = tuple(self[k].select(context.copy()) for k in range(1, len(self) - 1, 2))

    for results in product(*selectors):
        for i in range(len(results)):
            context.variables[self[i * 2][0].value] = results[i]
        if boolean_value(list(self[-1].select(context.copy()))):
            if some:
                return True
        elif not some:
            return False

    return not some


###
# 'for' expressions
@method('for', bp=20)
def nud(self):
    del self[:]
    while True:
        self.parser.next_token.expected('$')
        self.append(self.parser.expression(5))
        self.parser.advance('in')
        self.append(self.parser.expression(5))
        if self.parser.next_token.symbol == ',':
            self.parser.advance()
        else:
            break

    self.parser.advance('return')
    self.append(self.parser.expression(5))
    return self


@method('for')
def select(self, context=None):
    if context is not None:
        selectors = tuple(self[k].select(context.copy()) for k in range(1, len(self) - 1, 2))
        for results in product(*selectors):
            for i in range(len(results)):
                context.variables[self[i * 2][0].value] = results[i]
            for result in self[-1].select(context.copy()):
                yield result


###
# Sequence type based
@method('instance', bp=60)
@method('treat', bp=61)
def led(self, left):
    self.parser.advance('of' if self.symbol is 'instance' else 'as')
    if not self.parser.next_is_sequence_type_token():
        self.parser.next_token.wrong_syntax()
    self[:] = left, self.parser.expression(rbp=self.rbp)
    next_symbol = self.parser.next_token.symbol
    if self[1].symbol != 'empty-sequence' and next_symbol in ('?', '*', '+'):
        self[2:] = self.parser.symbol_table[next_symbol](self.parser),  # Add nullary token
        self.parser.advance()
    return self


@method('instance')
def evaluate(self, context=None):
    if self.parser.schema is None:
        self.missing_schema()
    occurs = self[2].symbol if len(self) > 2 else None
    position = None
    if self[1].symbol == 'empty-sequence':
        for _ in self[0].select(context):
            return False
        return True
    elif self[1].label == 'function':
        for position, item in enumerate(self[0].select(context)):
            if self[1].evaluate(context) is None:
                return False
            elif position and (occurs is None or occurs == '?'):
                return False
        else:
            return position is not None or occurs in ('*', '?')
    else:
        qname = prefixed_to_qname(self[1].source, self.parser.namespaces)
        for position, item in enumerate(self[0].select(context)):
            try:
                if not self.parser.schema.is_instance(item, qname):
                    return False
            except KeyError:
                self.missing_name("type %r not found in schema" % self[1].source)
            else:
                if position and (occurs is None or occurs == '?'):
                    return False
        else:
            return position is not None or occurs in ('*', '?')


@method('treat')
def evaluate(self, context=None):
    if self.parser.schema is None:
        self.missing_schema()
    occurs = self[2].symbol if len(self) > 2 else None
    position = None
    castable_expr = []
    if self[1].symbol == 'empty-sequence':
        for _ in self[0].select(context):
            self.wrong_sequence_type()
    elif self[1].label == 'function':
        for position, item in enumerate(self[0].select(context)):
            if self[1].evaluate(context) is None:
                self.wrong_sequence_type()
            elif position and (occurs is None or occurs == '?'):
                self.wrong_sequence_type("more than one item in sequence")
            castable_expr.append(item)
        else:
            if position is None and occurs not in ('*', '?'):
                self.wrong_sequence_type("the sequence cannot be empty")
    else:
        qname = prefixed_to_qname(self[1].source, self.parser.namespaces)
        for position, item in enumerate(self[0].select(context)):
            try:
                if not self.parser.schema.is_instance(item, qname):
                    self.wrong_sequence_type("item %r is not of type %r" % (item, self[1].source))
            except KeyError:
                self.missing_name("type %r not found in schema" % self[1].source)
            else:
                if position and (occurs is None or occurs == '?'):
                    self.wrong_sequence_type("more than one item in sequence")
                castable_expr.append(item)
        else:
            if position is None and occurs not in ('*', '?'):
                self.wrong_sequence_type("the sequence cannot be empty")

    return castable_expr


###
# Simple type based
@method('castable', bp=62)
@method('cast', bp=63)
def led(self, left):
    self.parser.advance('as')
    self[:] = left, self.parser.expression(rbp=self.rbp)
    if self.parser.next_token.symbol == '?':
        self[2:] = self.parser.symbol_table['?'](self.parser),  # Add nullary token
        self.parser.advance()
    return self


@method('castable')
@method('cast')
def evaluate(self, context=None):
    if self.parser.schema is None:
        self.missing_schema()
    atomic_type = prefixed_to_qname(self[1].source, namespaces=self.parser.namespaces)
    if atomic_type in (XSD_NOTATION, XSD_ANY_ATOMIC_TYPE):
        self.wrong_type("target type cannot be xs:NOTATION or xs:anyAtomicType [err:XPST0080]")

    result = [data_value(res) for res in self[0].select(context)]
    if len(result) > 1:
        if self.symbol != 'cast':
            return False
        self.wrong_context_type("more than one value in expression")
    elif not result:
        if len(self) == 3:
            return [] if self.symbol == 'cast' else True
        elif self.symbol != 'cast':
            return False
        else:
            self.wrong_value("atomic value is required")

    try:
        value = self.parser.schema.cast_as(result[0], atomic_type)
    except KeyError:
        self.unknown_atomic_type("atomic type %r not found in the in-scope schema types" % self[1].source)
    except TypeError as err:
        if self.symbol != 'cast':
            return False
        self.wrong_type(str(err))
    except ValueError as err:
        if self.symbol != 'cast':
            return False
        self.wrong_value(str(err))
    else:
        return value if self.symbol == 'cast' else True


###
# Comma operator - concatenate items or sequences
@method(infix(',', bp=5))
def evaluate(self, context=None):
    results = []
    for op in self:
        result = op.evaluate(context)
        if isinstance(result, list):
            results.extend(result)
        elif results is not None:
            results.append(result)
    return results


@method(',')
def select(self, context=None):
    for op in self:
        for result in op.select(context.copy() if context else None):
            yield result


###
# Parenthesized expressions: XPath 2.0 admits the empty case ().
@method('(')
def nud(self):
    if self.parser.next_token.symbol != ')':
        self[:] = self.parser.expression(),
    self.parser.advance(')')
    return self


@method('(')
def evaluate(self, context=None):
    if not self:
        return []
    else:
        return self[0].evaluate(context)


@method('(')
def select(self, context=None):
    if self:
        return self[0].select(context)
    else:
        return iter(())


###
# Value comparison operators
@method(infix('eq', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) == self[1].evaluate(context)


@method(infix('ne', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) != self[1].evaluate(context)


@method(infix('lt', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) < self[1].evaluate(context)


@method(infix('gt', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) > self[1].evaluate(context)


@method(infix('le', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) <= self[1].evaluate(context)


@method(infix('ge', bp=30))
def evaluate(self, context=None):
    return self[0].evaluate(context) >= self[1].evaluate(context)


###
# Node comparison
@method(infix('is', bp=30))
@method(infix('<<', bp=30))
@method(infix('>>', bp=30))
def evaluate(self, context=None):
    symbol = self.symbol

    left = list(self[0].select(context))
    if not left:
        return
    elif len(left) > 1 or not is_xpath_node(left[0]):
        self[0].wrong_type("left operand of %r must be a single node" % symbol)

    right = list(self[1].select(context))
    if not right:
        return
    elif len(right) > 1 or not is_xpath_node(right[0]):
        self[0].wrong_type("right operand of %r must be a single node" % symbol)

    if symbol == 'is':
        return left[0] is right[0]
    else:
        if left[0] is right[0]:
            return False
        for item in context.root.iter():
            if left[0] is item:
                return True if symbol == '<<' else False
            elif right[0] is item:
                return False if symbol == '<<' else True
        else:
            self.wrong_value("operands are not nodes of the XML tree!")


###
# Range expression
@method(infix('to', bp=35))
def evaluate(self, context=None):
    try:
        start = self[0].evaluate(context)
        stop = self[1].evaluate(context) + 1
    except TypeError as err:
        if context is not None:
            self.wrong_type(str(err))
        return
    else:
        return list(range(start, stop))


@method('to')
def select(self, context=None):
    for k in self.evaluate(context):
        yield k


###
# Numerical operators
@method(infix('idiv', bp=45))
def evaluate(self, context=None):
    return self[0].evaluate(context) // self[1].evaluate(context)


###
# Node types
@method(function('document-node', nargs=(0, 1)))
def evaluate(self, context=None):
    if context is not None:
        if context.item is None and is_document_node(context.root):
            if not self:
                return context.root
            elif is_element_node(context.root.getroot(), self[0].evaluate(context)):
                return context.root


@method(function('element', nargs=(0, 2)))
def evaluate(self, context=None):
    if context is not None:
        if not self:
            if is_element_node(context.item):
                return context.item
        elif is_element_node(context.item, self[1].evaluate(context)):
            return context.item


@method(function('schema-attribute', nargs=1))
def evaluate(self, context=None):
    attribute_name = self[0].source
    qname = prefixed_to_qname(attribute_name, self.parser.namespaces)
    if self.parser.schema.get_attribute(qname) is None:
        self.missing_name("attribute %r not found in schema" % attribute_name)

    if context is not None:
        if is_attribute_node(context.item, qname):
            return context.item


@method(function('schema-element', nargs=1))
def evaluate(self, context=None):
    element_name = self[0].source
    qname = prefixed_to_qname(element_name, self.parser.namespaces)
    if self.parser.schema.get_element(qname) is None \
            and self.parser.schema.get_substitution_group(qname) is None:
        self.missing_name("element %r not found in schema" % element_name)

    if context is not None:
        if is_element_node(context.item) and context.item.tag == qname:
            return context.item


@method(function('empty-sequence', nargs=0))
def evaluate(self, context=None):
    if context is not None:
        return isinstance(context.item, list) and not context.item


@method('document-node')
@method('element')
@method('schema-attribute')
@method('schema-element')
@method('empty-sequence')
def select(self, context=None):
    if context is not None:
        for _ in context.iter_children_or_self():
            item = self.evaluate(context)
            if item is not None:
                yield item


###
# Function for QNames
@method(function('QName', nargs=2))
def evaluate(self, context=None):
    uri = self.get_argument(context)
    if uri is None:
        uri = ''
    elif not isinstance(uri, string_base_type):
        raise self.error('FORG0006', '1st argument has an invalid type %r' % type(uri))

    qname = self[1].evaluate(context)
    if not isinstance(qname, string_base_type):
        raise self.error('FORG0006', '2nd argument has an invalid type %r' % type(qname))
    match = QNAME_PATTERN.match(qname)
    if match is None:
        raise self.error('FOCA0002', '2nd argument must be an xs:QName')

    pfx = match.groupdict()['prefix'] or ''
    if not uri:
        if pfx:
            raise self.error('FOCA0002', 'must be a local name when the parameter URI is empty')
    else:
        try:
            if uri != self.parser.namespaces[pfx]:
                raise self.error('FOCA0002', 'prefix %r is already is used for another namespace' % pfx)
        except KeyError:
            self.parser.namespaces[pfx] = uri
    return qname


@method(function('prefix-from-QName', nargs=1))
def evaluate(self, context=None):
    qname = self.get_argument(context)
    if qname is None:
        return []
    elif not isinstance(qname, string_base_type):
        raise self.error('FORG0006', 'argument has an invalid type %r' % type(qname))
    match = QNAME_PATTERN.match(qname)
    if match is None:
        raise self.error('FOCA0002', 'argument must be an xs:QName')
    return match.groupdict()['prefix'] or []


@method(function('local-name-from-QName', nargs=1))
def evaluate(self, context=None):
    qname = self.get_argument(context)
    if qname is None:
        return []
    elif not isinstance(qname, string_base_type):
        raise self.error('FORG0006', 'argument has an invalid type %r' % type(qname))
    match = QNAME_PATTERN.match(qname)
    if match is None:
        raise self.error('FOCA0002', 'argument must be an xs:QName')
    return match.groupdict()['local']


@method(function('namespace-uri-from-QName', nargs=1))
def evaluate(self, context=None):
    qname = self.get_argument(context)
    if qname is None:
        return []
    elif not isinstance(qname, string_base_type):
        raise self.error('FORG0006', 'argument has an invalid type %r' % type(qname))
    elif not qname:
        return ''

    match = QNAME_PATTERN.match(qname)
    if match is None:
        raise self.error('FOCA0002', 'argument must be an xs:QName')
    try:
        return self.parser.namespaces[match.groupdict()['prefix'] or '']
    except KeyError as err:
        raise self.error('FONS0004', 'No namespace found for prefix %s' % str(err))


@method(function('namespace-uri-for-prefix', nargs=2))
def evaluate(self, context=None):
    if context is not None:
        pfx = self.get_argument(context.copy())
        if pfx is None:
            pfx = ''
        if not isinstance(pfx, string_base_type):
            raise self.error('FORG0006', '1st argument has an invalid type %r' % type(pfx))

        elem = self.get_argument(context, index=1)
        if not is_element_node(elem):
            raise self.error('FORG0006', '2nd argument %r is not a node' % elem)
        ns_uris = {get_namespace(e.tag) for e in elem.iter()}
        for p, uri in self.parser.namespaces.items():
            if uri in ns_uris:
                if p == pfx:
                    return uri
        return []


@method(function('in-scope-prefixes', nargs=1))
def select(self, context=None):
    if context is not None:
        elem = self.get_argument(context)
        if not is_element_node(elem):
            raise self.error('FORG0006', 'argument %r is not a node' % elem)
        for e in elem.iter():
            tag_ns = get_namespace(e.tag)
            for pfx, uri in self.parser.namespaces.items():
                if uri == tag_ns:
                    yield pfx


@method(function('resolve-QName', nargs=2))
def evaluate(self, context=None):
    if context is not None:
        qname = self.get_argument(context.copy())
        if qname is None:
            return []
        if not isinstance(qname, string_base_type):
            raise self.error('FORG0006', '1st argument has an invalid type %r' % type(qname))
        match = QNAME_PATTERN.match(qname)
        if match is None:
            raise self.error('FOCA0002', '1st argument must be an xs:QName')
        pfx = match.groupdict()['prefix'] or ''

        elem = self.get_argument(context, index=1)
        if not is_element_node(elem):
            raise self.error('FORG0006', '2nd argument %r is not a node' % elem)
        ns_uris = {get_namespace(e.tag) for e in elem.iter()}
        for p, uri in self.parser.namespaces.items():
            if uri in ns_uris:
                if p == pfx:
                    return '{%s}%s' % (uri, match.groupdict()['local']) if uri else match.groupdict()['local']
        raise self.error('FONS0004', 'No namespace found for prefix %r' % pfx)


###
# XSD constructor functions
@method(constructor('string1'))
def evaluate(self, context=None):
    return None if context is None else str(self.get_argument(context))


@method(constructor('normalizedString'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    return [] if item is None else str(item).replace('\t', ' ').replace('\n', ' ')


@method(constructor('token'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    return [] if item is None else collapse_white_spaces(item)


@method(constructor('language'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    else:
        match = LANGUAGE_CODE_PATTERN.match(collapse_white_spaces(item))
        if match is None:
            raise self.error('FOCA0002', "%r is not a language code" % item)
        return match.group()


@method(constructor('NMTOKEN'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    else:
        match = NMTOKEN_PATTERN.match(collapse_white_spaces(item))
        if match is None:
            raise self.error('FOCA0002', "%r is not an xs:NMTOKEN value" % item)
        return match.group()


@method(constructor('Name'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    else:
        match = NAME_PATTERN.match(collapse_white_spaces(item))
        if match is None:
            raise self.error('FOCA0002', "%r is not an xs:Name value" % item)
        return match.group()


@method(constructor('NCName'))
@method(constructor('ID'))
@method(constructor('IDREF'))
@method(constructor('ENTITY'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    else:
        match = NCNAME_PATTERN.match(collapse_white_spaces(item))
        if match is None:
            raise self.error('FOCA0002', "%r is not an xs:%s value" % (item, self.symbol))
        return match.group()


@method(constructor('anyURI'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    uri = collapse_white_spaces(item)
    try:
        urlparse(uri)
    except URLError:
        raise self.error('FOCA0002', "%r is not an xs:anyURI value" % item)
    if uri.count('#') > 1:
        raise self.error('FOCA0002', "%r is not an xs:anyURI value (too many # characters)" % item)
    elif WRONG_ESCAPE_PATTERN.search(uri):
        raise self.error('FOCA0002', "%r is not an xs:anyURI value (wrong escaping)" % item)
    return uri


@method(constructor('decimal'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    try:
        return [] if item is None else decimal.Decimal(item)
    except (ValueError, decimal.DecimalException) as err:
        raise self.error("FORG0001", str(err))


@method(constructor('integer'))
def evaluate(self, context=None):
    return self.integer(context)


@method(constructor('nonNegativeInteger'))
def evaluate(self, context=None):
    return self.integer(context, 0)


@method(constructor('positiveInteger'))
def evaluate(self, context=None):
    return self.integer(context, 1)


@method(constructor('nonPositiveInteger'))
def evaluate(self, context=None):
    return self.integer(context, higher_bound=1)


@method(constructor('negativeInteger'))
def evaluate(self, context=None):
    return self.integer(context, higher_bound=0)


@method(constructor('long'))
def evaluate(self, context=None):
    return self.integer(context, -2**127, 2**127)


@method(constructor('int'))
def evaluate(self, context=None):
    return self.integer(context, -2**63, 2**63)


@method(constructor('short'))
def evaluate(self, context=None):
    return self.integer(context, -2**15, 2**15)


@method(constructor('byte'))
def evaluate(self, context=None):
    return self.integer(context, -2**7, 2**7)


@method(constructor('unsignedLong'))
def evaluate(self, context=None):
    return self.integer(context, 0, 2**128)


@method(constructor('unsignedInt'))
def evaluate(self, context=None):
    return self.integer(context, 0, 2**64)


@method(constructor('unsignedShort'))
def evaluate(self, context=None):
    return self.integer(context, 0, 2**16)


@method(constructor('unsignedByte'))
def evaluate(self, context=None):
    return self.integer(context, 0, 2**8)


@method(constructor('double'))
@method(constructor('float'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    try:
        return [] if item is None else float(item)
    except ValueError as err:
        raise self.error("FORG0001", str(err))


@method(constructor('dateTime'))
def evaluate(self, context=None):
    return self.datetime(context, DateTime)


@method(constructor('date'))
def evaluate(self, context=None):
    return self.datetime(context, Date)


@method(constructor('gDay'))
def evaluate(self, context=None):
    return self.datetime(context, GregorianDay)


@method(constructor('gMonth'))
def evaluate(self, context=None):
    return self.datetime(context, GregorianMonth)


@method(constructor('gMonthDay'))
def evaluate(self, context=None):
    return self.datetime(context, GregorianMonthDay)


@method(constructor('gYear'))
def evaluate(self, context=None):
    return self.datetime(context, GregorianYear)


@method(constructor('gYearMonth'))
def evaluate(self, context=None):
    return self.datetime(context, GregorianYearMonth)


@method(constructor('time'))
def evaluate(self, context=None):
    return self.datetime(context, Time)


@method(constructor('duration'))
def evaluate(self, context=None):
    return self.duration(context, Duration)


@method(constructor('yearMonthDuration'))
def evaluate(self, context=None):
    return self.duration(context, YearMonthDuration)


@method(constructor('dayTimeDuration'))
def evaluate(self, context=None):
    return self.duration(context, DayTimeDuration)


@method(constructor('base64Binary'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    elif isinstance(item, UntypedAtomic):
        return codecs.encode(unicode_type(item), 'base64')
    elif not isinstance(item, (bytes, unicode_type)):
        raise self.error('FORG0006', 'the argument has an invalid type %r' % type(item))
    elif not isinstance(item, bytes) or self[0].label == 'literal':
        return codecs.encode(item.encode('ascii'), 'base64')
    elif HEX_BINARY_PATTERN.search(item.decode('utf-8')):
        value = codecs.decode(item, 'hex') if str is not bytes else item
        return codecs.encode(value, 'base64')
    elif NOT_BASE64_BINARY_PATTERN.search(item.decode('utf-8')):
        return codecs.encode(item, 'base64')
    else:
        return item


@method(constructor('hexBinary'))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        return []
    elif isinstance(item, UntypedAtomic):
        return codecs.encode(unicode_type(item), 'hex')
    elif not isinstance(item, (bytes, unicode_type)):
        raise self.error('FORG0006', 'the argument has an invalid type %r' % type(item))
    elif not isinstance(item, bytes) or self[0].label == 'literal':
        return codecs.encode(item.encode('ascii'), 'hex')
    elif HEX_BINARY_PATTERN.search(item.decode('utf-8')):
        return item if isinstance(item, bytes) or str is bytes else codecs.encode(item.encode('ascii'), 'hex')
    else:
        try:
            value = codecs.decode(item, 'base64')
        except ValueError:
            return codecs.encode(item, 'hex')
        else:
            return codecs.encode(value, 'hex')


###
# Context item
@method(function('item', nargs=0))
def evaluate(self, context=None):
    if context is None:
        return
    elif context.item is None:
        return context.root
    else:
        return context.item


###
# Accessor functions
@method(function('node-name', nargs=1))
def evaluate(self, context=None):
    return node_name(self.get_argument(context))


@method(function('nilled', nargs=1))
def evaluate(self, context=None):
    return node_nilled(self.get_argument(context))


@method(function('data', nargs=1))
def select(self, context=None):
    for item in self[0].select(context):
        value = data_value(item)
        if value is None:
            self.wrong_type("argument node does not have a typed value [err:FOTY0012]")
        else:
            yield value


@method(function('base-uri', nargs=(0, 1)))
def evaluate(self, context=None):
    item = self.get_argument(context)
    if item is None:
        self.missing_context("context item is undefined")
    elif not is_xpath_node(item):
        self.wrong_context_type("context item is not a node")
    else:
        return node_base_uri


@method(function('document-uri', nargs=1))
def evaluate(self, context=None):
    return node_document_uri(self.get_argument(context))


###
# Number functions
@method(function('round-half-to-even', nargs=(1, 2)))
def evaluate(self, context=None):
    item = self.get_argument(context)
    try:
        precision = 0 if len(self) < 2 else self[1].evaluate(context)
        if PY3 or precision < 0:
            value = round(decimal.Decimal(item), precision)
        else:
            number = decimal.Decimal(item)
            exp = decimal.Decimal('1' if not precision else '.%s1' % ('0' * (precision - 1)))
            value = float(number.quantize(exp, rounding='ROUND_HALF_EVEN'))
    except TypeError as err:
        if item is not None and not isinstance(item, list):
            self.wrong_type(str(err))
    except decimal.DecimalException as err:
        if item is not None and not isinstance(item, list):
            self.wrong_value(str(err))
    else:
        return float(value)


@method(function('abs', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context)
    try:
        return abs(node_string_value(item) if is_xpath_node(item) else item)
    except TypeError:
        return float('nan')


###
# General functions for sequences
@method(function('empty', nargs=1))
@method(function('exists', nargs=1))
def evaluate(self, context=None):
    return next(iter(self.select(context)))


@method('empty')
def select(self, context=None):
    try:
        next(iter(self[0].select(context)))
    except StopIteration:
        yield True
    else:
        yield False


@method('exists')
def select(self, context=None):
    try:
        next(iter(self[0].select(context)))
    except StopIteration:
        yield False
    else:
        yield True


@method(function('distinct-values', nargs=(1, 2)))
def select(self, context=None):
    nan = False
    results = []
    for item in self[0].select(context):
        value = data_value(item)
        if context is not None:
            context.item = value
        if not nan and isinstance(value, float) and math.isnan(value):
            yield value
            nan = True
        elif value not in results:
            yield value
            results.append(value)


@method(function('insert-before', nargs=3))
def select(self, context=None):
    insert_at_pos = max(0, self[1].value - 1)
    inserted = False
    for pos, result in enumerate(self[0].select(context)):
        if not inserted and pos == insert_at_pos:
            for item in self[2].select(context):
                yield item
            inserted = True
        yield result

    if not inserted:
        for item in self[2].select(context):
            yield item


@method(function('index-of', nargs=(1, 3)))
def select(self, context=None):
    value = self[1].evaluate(context)
    for pos, result in enumerate(self[0].select(context)):
        if result == value:
            yield pos + 1


@method(function('remove', nargs=2))
def select(self, context=None):
    target = self[1].evaluate(context) - 1
    for pos, result in enumerate(self[0].select(context)):
        if pos != target:
            yield result


@method(function('reverse', nargs=1))
def select(self, context=None):
    for result in reversed(list(self[0].select(context))):
        yield result


@method(function('subsequence', nargs=(2, 3)))
def select(self, context=None):
    starting_loc = self[1].evaluate(context) - 1
    length = self[2].evaluate(context) if len(self) >= 3 else 0
    for pos, result in enumerate(self[0].select(context)):
        if starting_loc <= pos and (not length or pos < starting_loc + length):
            yield result


@method(function('unordered', nargs=1))
def select(self, context=None):
    for result in sorted(list(self[0].select(context)), key=lambda x: string_value(x)):
        yield result


###
# Cardinality functions for sequences
@method(function('zero-or-one', nargs=1))
def select(self, context=None):
    results = iter(self[0].select(context))
    try:
        item = next(results)
    except StopIteration:
        return

    try:
        next(results)
    except StopIteration:
        yield item
    else:
        self.wrong_value("called with a sequence containing more than one item [err:FORG0003]")


@method(function('one-or-more', nargs=1))
def select(self, context=None):
    results = iter(self[0].select(context))
    try:
        item = next(results)
    except StopIteration:
        self.wrong_value("called with a sequence containing no items [err:FORG0004]")
    else:
        yield item
        while True:
            try:
                yield next(results)
            except StopIteration:
                break


@method(function('exactly-one', nargs=1))
def select(self, context=None):
    results = iter(self[0].select(context))
    try:
        item = next(results)
    except StopIteration:
        self.wrong_value("called with a sequence containing zero items [err:FORG0005]")
    else:
        try:
            next(results)
        except StopIteration:
            yield item
        else:
            self.wrong_value("called with a sequence containing more than one item [err:FORG0005]")


###
# String functions
@method(function('codepoints-to-string', nargs=1))
def evaluate(self, context=None):
    return ''.join(unicode_chr(cp) for cp in self[0].select(context))


@method(function('string-to-codepoints', nargs=1))
def select(self, context=None):
    for char in self[0].evaluate(context):
        yield ord(char)


@method(function('compare', nargs=(2, 3)))
def evaluate(self, context=None):
    raise NotImplementedError()


@method(function('codepoint-equal', nargs=2))
def evaluate(self, context=None):
    raise NotImplementedError()


@method(function('string-join', nargs=2))
def evaluate(self, context=None):
    try:
        return self[1].evaluate(context).join(s for s in self[0].select(context))
    except AttributeError as err:
        self.wrong_type("the separator must be a string: %s" % err)
    except TypeError as err:
        self.wrong_type("the values must be strings: %s" % err)


@method(function('normalize-unicode', nargs=(1, 2)))
def evaluate(self, context=None):
    raise NotImplementedError()


@method(function('upper-case', nargs=1))
def evaluate(self, context=None):
    arg = self.get_argument(context)
    try:
        return '' if arg is None else arg.upper()
    except AttributeError:
        self.wrong_type("the argument must be a string: %r" % arg)


@method(function('lower-case', nargs=1))
def evaluate(self, context=None):
    arg = self.get_argument(context)
    try:
        return '' if arg is None else arg.lower()
    except AttributeError:
        self.wrong_type("the argument must be a string: %r" % arg)


@method(function('encode-for-uri', nargs=1))
def evaluate(self, context=None):
    uri_part = self.get_argument(context)
    try:
        return '' if uri_part is None else urllib_quote(uri_part, safe='~')
    except TypeError:
        self.wrong_type("the argument must be a string: %r" % uri_part)


@method(function('iri-to-uri', nargs=1))
def evaluate(self, context=None):
    iri = self.get_argument(context)
    try:
        return '' if iri is None else urllib_quote(iri, safe='-_.!~*\'()#;/?:@&=+$,[]%')
    except TypeError:
        self.wrong_type("the argument must be a string: %r" % iri)


@method(function('escape-html-uri', nargs=1))
def evaluate(self, context=None):
    uri = self.get_argument(context)
    try:
        return '' if uri is None else urllib_quote(uri, safe=''.join(chr(cp) for cp in range(32, 127)))
    except TypeError:
        self.wrong_type("the argument must be a string: %r" % uri)


@method(function('starts-with', nargs=(2, 3)))
def evaluate(self, context=None):
    arg1 = self.get_argument(context)
    arg2 = self.get_argument(context, index=1)
    try:
        return arg1.startswith(arg2)
    except TypeError:
        self.wrong_type("the arguments must be a string")


@method(function('ends-with', nargs=(2, 3)))
def evaluate(self, context=None):
    arg1 = self.get_argument(context)
    arg2 = self.get_argument(context, index=1)
    try:
        return arg1.endswith(arg2)
    except TypeError:
        self.wrong_type("the arguments must be a string")


###
# Functions on durations, dates and times
@method(function('years-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.months // 12 if item.months >= 0 else -(abs(item.months) // 12)


@method(function('months-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.months % 12 if item.months >= 0 else -(abs(item.months) % 12)


@method(function('days-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.seconds // 86400 if item.seconds >= 0 else -(abs(item.seconds) // 86400)


@method(function('hours-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.seconds // 3600 % 24 if item.seconds >= 0 else -(abs(item.seconds) // 3600 % 24)


@method(function('minutes-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.seconds // 60 % 60 if item.seconds >= 0 else -(abs(item.seconds) // 60 % 60)


@method(function('seconds-from-duration', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Duration)
    if item is None:
        return []
    else:
        return item.seconds % 60 if item.seconds >= 0 else -(abs(item.seconds) % 60)


@method(function('year-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else -(item.dt.year + 1) if item.bce else item.dt.year


@method(function('month-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else item.dt.month


@method(function('day-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else item.dt.day


@method(function('hours-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else item.dt.hour


@method(function('minutes-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else item.dt.minute


@method(function('seconds-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else item.dt.second


@method(function('timezone-from-dateTime', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=DateTime)
    return [] if item is None else DayTimeDuration(seconds=item.tzinfo.offset.total_seconds())


@method(function('year-from-date', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Date)
    return [] if item is None else item.year


@method(function('month-from-date', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Date)
    return [] if item is None else item.month


@method(function('day-from-date', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Date)
    return [] if item is None else item.day


@method(function('timezone-from-date', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Date)
    return [] if item is None else DayTimeDuration(seconds=item.tzinfo.offset.total_seconds())


@method(function('hours-from-time', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Time)
    return [] if item is None else item.hour


@method(function('minutes-from-time', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Time)
    return [] if item is None else item.minute


@method(function('seconds-from-time', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Time)
    return [] if item is None else item.second + item.microsecond / 1000000.0


@method(function('timezone-from-time', nargs=1))
def evaluate(self, context=None):
    item = self.get_argument(context, cls=Time)
    return [] if item is None else DayTimeDuration(seconds=item.tzinfo.offset.total_seconds())


###
# Multi role-tokens

# unregister('boolean')

@method(constructor('boolean1'))
def evaluate(self, context=None):
    if self.label == 'function':
        return boolean_value(self[0].get_results(context))
    item = self.get_argument(context)
    if item is None:
        return []
    elif not isinstance(item, string_base_type):
        raise self.error('FORG0006', 'the argument has an invalid type %r' % type(item))
    elif item in ('true', '1'):
        return True
    elif item in ('false', '0'):
        return False
    else:
        raise self.error('FOCA0002', "%r: not a boolean value" % item)


register('boolean1', label=('function', 'constructor'))

###
# Example of token redefinition and how-to create a multi-role token.
#
# In XPath 2.0 the 'attribute' keyword is used not only for the attribute:: axis but also for
# attribute() node type function.
###
unregister('attribute')
register('attribute', lbp=90, rbp=90, label=('function', 'axis'),
         pattern=r'\battribute(?=\s*\:\:|\s*\(\:.*\:\)\s*\:\:|\s*\(|\s*\(\:.*\:\)\()')


@method('attribute')
def nud(self):
    if self.parser.next_token.symbol == '::':
        self.parser.advance('::')
        self.parser.next_token.expected(
            '(name)', '*', 'text', 'node', 'document-node', 'comment', 'processing-instruction',
            'attribute', 'schema-attribute', 'element', 'schema-element'
        )
        self[:] = self.parser.expression(rbp=90),
        self.label = 'axis'
    else:
        self.parser.advance('(')
        if self.parser.next_token.symbol != ')':
            self[:] = self.parser.expression(5),
            if self.parser.next_token.symbol == ',':
                self.parser.advance(',')
                self[1:] = self.parser.expression(5),
        self.parser.advance(')')
        self.label = 'function'
    return self


@method('attribute')
def select(self, context=None):
    if context is None:
        return
    elif self.label == 'axis':
        for _ in context.iter_attributes():
            for result in self[0].select(context):
                yield result
    else:
        attribute_name = self[0].evaluate(context) if self else None
        for item in context.iter_attributes():
            if is_attribute_node(item, attribute_name):
                yield context.item[1]


@method('attribute')
def evaluate(self, context=None):
    if context is not None:
        if is_attribute_node(context.item, self[0].evaluate(context) if self else None):
            return context.item[1]


###
# Timezone adjustment functions
@method(function('adjust-dateTime-to-timezone', nargs=(1, 2)))
def evaluate(self, context=None):
    return self.adjust_datetime(context, DateTime)


@method(function('adjust-date-to-timezone', nargs=(1, 2)))
def evaluate(self, context=None):
    return self.adjust_datetime(context, Date)


@method(function('adjust-time-to-timezone', nargs=(1, 2)))
def evaluate(self, context=None):
    return self.adjust_datetime(context, Time)


###
# Context functions
@method(function('current-dateTime', nargs=0))
def evaluate(self, context=None):
    if context is not None:
        return DateTime(context.current_dt)


@method(function('current-date', nargs=0))
def evaluate(self, context=None):
    if context is not None:
        return Date(context.current_dt.replace(hour=0, minute=0, second=0, microsecond=0))


@method(function('current-time', nargs=0))
def evaluate(self, context=None):
    if context is not None:
        return Time(context.current_dt.replace(year=1900, month=1, day=1))


@method(function('implicit-timezone', nargs=0))
def evaluate(self, context=None):
    if context is not None and context.timezone is not None:
        return context.timezone
    else:
        return Timezone(datetime.timedelta(seconds=time.timezone))


###
# The root function (Ref: https://www.w3.org/TR/2010/REC-xpath-functions-20101214/#func-root)
@method(function('root', nargs=(0, 1)))
def evaluate(self, context=None):
    if self:
        item = self.get_argument(context)
    elif context is None:
        raise self.error('XPDY0002')
    else:
        item = context.item

    if item is None:
        return []
    elif is_xpath_node(item):
        return item
    else:
        raise self.error('XPTY0004')


###
# The error function (Ref: https://www.w3.org/TR/xpath20/#func-error)
@method(function('error', nargs=(0, 3)))
def evaluate(self, context=None):
    if not self:
        raise self.error('FOER0000')
    elif len(self) == 1:
        item = self.get_argument(context)
        raise self.error(item or 'FOER0000')


XPath2Parser.build_tokenizer()
