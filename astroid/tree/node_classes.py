# Copyright (c) 2009-2011, 2013-2014 LOGILAB S.A. (Paris, FRANCE) <contact@logilab.fr>
# Copyright (c) 2014-2016 Claudiu Popa <pcmanticore@gmail.com>
# Copyright (c) 2015-2016 Cara Vinson <ceridwenv@gmail.com>

# Licensed under the LGPL: https://www.gnu.org/licenses/old-licenses/lgpl-2.1.en.html
# For details: https://github.com/PyCQA/astroid/blob/master/COPYING.LESSER

"""Module for some node classes. More nodes in scoped_nodes.py
"""

import functools
import warnings
import sys

import six

from astroid import context as contextmod
from astroid import decorators
from astroid import exceptions
from astroid import inference
from astroid.interpreter import runtimeabc
from astroid.interpreter import objects
from astroid import manager
from astroid import protocols
from astroid.tree import base
from astroid.tree import treeabc
from astroid import util

raw_building = util.lazy_import('raw_building')

BUILTINS = six.moves.builtins.__name__
MANAGER = manager.AstroidManager()


# getitem() helpers.

_SLICE_SENTINEL = object()


def _slice_value(index, context=None):
    """Get the value of the given slice index."""

    if isinstance(index, Const):
        if isinstance(index.value, (int, type(None))):
            return index.value
    elif index is None:
        return None
    else:
        # Try to infer what the index actually is.
        # Since we can't return all the possible values,
        # we'll stop at the first possible value.
        try:
            inferred = next(index.infer(context=context))
        except exceptions.InferenceError:
            pass
        else:
            if isinstance(inferred, Const):
                if isinstance(inferred.value, (int, type(None))):
                    return inferred.value

    # Use a sentinel, because None can be a valid
    # value that this function can return,
    # as it is the case for unspecified bounds.
    return _SLICE_SENTINEL


def _infer_slice(node, context=None):
    lower = _slice_value(node.lower, context)
    upper = _slice_value(node.upper, context)
    step = _slice_value(node.step, context)
    if all(elem is not _SLICE_SENTINEL for elem in (lower, upper, step)):
        return slice(lower, upper, step)

    raise TypeError('Could not infer slice used in subscript.')


def _container_getitem(instance, elts, index, context=None):
    """Get a slice or an item, using the given *index*, for the given sequence."""
    if isinstance(index, Slice):
        index_slice = _infer_slice(index, context=context)
        new_cls = instance.__class__()
        new_cls.elts = elts[index_slice]
        new_cls.parent = instance.parent
        return new_cls
    elif isinstance(index, Const):
        return elts[index.value]

    raise TypeError('Could not use %s as subscript index' % index)

@util.register_implementation(treeabc.Statement)
class Statement(base.NodeNG):
    """Statement node adding a few attributes"""
    is_statement = True

    def next_sibling(self):
        """return the next sibling statement"""
        stmts = self.parent.child_sequence(self)
        index = stmts.index(self)
        try:
            return stmts[index +1]
        except IndexError:
            pass

    def previous_sibling(self):
        """return the previous sibling statement"""
        stmts = self.parent.child_sequence(self)
        index = stmts.index(self)
        if index >= 1:
            return stmts[index -1]


class AssignedStmtsMixin(object):
    """Provide an `assigned_stmts` method to classes which inherits it."""

    def assigned_stmts(self, node=None, context=None, assign_path=None):
        """Responsible to return the assigned statement
        (e.g. not inferred) according to the assignment type.

        The `assign_path` parameter is used to record the lhs path of the original node.
        For instance if we want assigned statements for 'c' in 'a, (b,c)', assign_path
        will be [1, 1] once arrived to the Assign node.

        The `context` parameter is the current inference context which should be given
        to any intermediary inference necessary.
        """
        # Inject the current module into assigned_stmts, in order to avoid
        # circular dependencies between these modules.
        return protocols.assigned_stmts(self, sys.modules[__name__],
                                        node=node, context=context,
                                        assign_path=assign_path)


# Name classes


class BaseAssignName(base.LookupMixIn, base.ParentAssignTypeMixin,
                     AssignedStmtsMixin, base.NodeNG):
    _other_fields = ('name',)

    def __init__(self, name=None, lineno=None, col_offset=None, parent=None):
        self.name = name
        super(BaseAssignName, self).__init__(lineno, col_offset, parent)

    infer_lhs = inference.infer_name

@util.register_implementation(treeabc.AssignName)
class AssignName(BaseAssignName):
    """class representing an AssignName node"""


@util.register_implementation(treeabc.Parameter)
class Parameter(BaseAssignName):

    _astroid_fields = ('default', 'annotation')
    _other_fields = ('name', )

    def __init__(self, name=None, lineno=None, col_offset=None, parent=None):
        super(Parameter, self).__init__(name=name, lineno=lineno,
                                        col_offset=col_offset, parent=parent)

    def postinit(self, default, annotation):
        self.default = default
        self.annotation = annotation


@util.register_implementation(treeabc.DelName)
class DelName(base.LookupMixIn, base.ParentAssignTypeMixin, base.NodeNG):
    """class representing a DelName node"""
    _other_fields = ('name',)

    def __init__(self, name=None, lineno=None, col_offset=None, parent=None):
        self.name = name
        super(DelName, self).__init__(lineno, col_offset, parent)


@util.register_implementation(treeabc.Name)
class Name(base.LookupMixIn, base.NodeNG):
    """class representing a Name node"""
    _other_fields = ('name',)

    def __init__(self, name=None, lineno=None, col_offset=None, parent=None):
        self.name = name
        super(Name, self).__init__(lineno, col_offset, parent)
    

@util.register_implementation(treeabc.Arguments)
class Arguments(base.AssignTypeMixin, AssignedStmtsMixin, base.NodeNG):
    """class representing an Arguments node"""

    _astroid_fields = ('args', 'vararg', 'kwarg', 'keyword_only', 'positional_only')

    def __init__(self, parent=None):
        # We don't want lineno and col_offset from the parent's __init__.
        super(Arguments, self).__init__(parent=parent)

    def postinit(self, args, vararg, kwarg, keyword_only, positional_only):
        self.args = args
        self.vararg = vararg
        self.kwarg = kwarg
        self.keyword_only = keyword_only
        self.positional_only = positional_only
        self.positional_and_keyword = self.args + self.positional_only

    def _infer_name(self, frame, name):
        if self.parent is frame:
            return name
        return None

    @decorators.cachedproperty
    def fromlineno(self):
        # Let the Function's lineno be the lineno for this.
        if self.parent.fromlineno:
            return self.parent.fromlineno

        return super(Arguments, self).fromlineno

    def format_args(self):
        """return arguments formatted as string"""
        result = []
        if self.positional_and_keyword:
            result.append(_format_args(self.positional_and_keyword))
        if self.vararg:
            result.append('*%s' % _format_args((self.vararg, )))
        if self.keyword_only:
            if not self.vararg:
                result.append('*')
            result.append(_format_args(self.keyword_only))
        if self.kwarg:
            result.append('**%s' % _format_args((self.kwarg, )))
        return ', '.join(result)

    def default_value(self, argname):
        """return the default value for an argument

        :raise `NoDefault`: if there is no default value defined
        """
        for place in (self.positional_and_keyword, self.keyword_only):
            i = _find_arg(argname, place)[0]
            if i is not None:
                value = place[i]
                if not value.default:
                    continue
                return value.default

        raise exceptions.NoDefault(func=self.parent, name=argname)

    def is_argument(self, name):
        """return True if the name is defined in arguments"""
        if self.vararg and name == self.vararg.name:
            return True
        if self.kwarg and name == self.kwarg.name:
            return True
        return self.find_argname(name, True)[1] is not None

    def find_argname(self, argname, rec=False):
        """return index and Name node with given name"""
        if self.positional_and_keyword: # self.args may be None in some cases (builtin function)
            return _find_arg(argname, self.positional_and_keyword, rec)
        return None, None

    def get_children(self):
        """override get_children to skip over None elements in kw_defaults"""
        for child in super(Arguments, self).get_children():
            if child is not None:
                yield child


def _find_arg(argname, args, rec=False):
    for i, arg in enumerate(args):
        if isinstance(arg, Tuple):
            if rec:
                found = _find_arg(argname, arg.elts)
                if found[0] is not None:
                    return found
        elif arg.name == argname:
            return i, arg
    return None, None


def _format_args(args):
    values = []
    if not args:
        return ''
    for i, arg in enumerate(args):
        if isinstance(arg, Tuple):
            values.append('(%s)' % _format_args(arg.elts))
        else:
            argname = arg.name
            annotation = arg.annotation
            if annotation:
                argname += ':' + annotation.as_string()
            values.append(argname)
            
            default = arg.default
            if default:
                values[-1] += '=' + default.as_string()

    return ', '.join(values)


@util.register_implementation(treeabc.Unknown)
class Unknown(base.NodeNG):
    '''This node represents a node in a constructed AST where
    introspection is not possible.  At the moment, it's only used in
    the args attribute of FunctionDef nodes where function signature
    introspection failed.

    '''
    def infer(self, context=None, **kwargs):
        '''Inference on an Unknown node immediately terminates.'''
        yield util.Uninferable


@util.register_implementation(treeabc.AssignAttr)
class AssignAttr(base.ParentAssignTypeMixin,
                 AssignedStmtsMixin, base.NodeNG):
    """class representing an AssignAttr node"""
    _astroid_fields = ('expr',)
    _other_fields = ('attrname',)
    expr = None

    def __init__(self, attrname=None, lineno=None, col_offset=None, parent=None):
        self.attrname = attrname
        super(AssignAttr, self).__init__(lineno, col_offset, parent)

    def postinit(self, expr=None):
        self.expr = expr

    infer_lhs = inference.infer_attribute


@util.register_implementation(treeabc.Assert)
class Assert(Statement):
    """class representing an Assert node"""
    _astroid_fields = ('test', 'fail',)
    test = None
    fail = None

    def postinit(self, test=None, fail=None):
        self.fail = fail
        self.test = test


@util.register_implementation(treeabc.Assign)
class Assign(base.AssignTypeMixin, AssignedStmtsMixin, Statement):
    """class representing an Assign node"""
    _astroid_fields = ('targets', 'value',)
    targets = None
    value = None

    def postinit(self, targets=None, value=None):
        self.targets = targets
        self.value = value


@util.register_implementation(treeabc.AugAssign)
class AugAssign(base.AssignTypeMixin, AssignedStmtsMixin, Statement):
    """class representing an AugAssign node"""
    _astroid_fields = ('target', 'value')
    _other_fields = ('op',)
    target = None
    value = None

    def __init__(self, op=None, lineno=None, col_offset=None, parent=None):
        self.op = op
        super(AugAssign, self).__init__(lineno, col_offset, parent)

    def postinit(self, target=None, value=None):
        self.target = target
        self.value = value

    def _infer_augassign(self, context):
        return inference.infer_augassign(self, nodes=sys.modules[__name__],
                                         context=context)

    def type_errors(self, context=None):
        """Return a list of TypeErrors which can occur during inference.

        Each TypeError is represented by a :class:`BinaryOperationError`,
        which holds the original exception.
        """
        try:
            results = self._infer_augassign(context=context)
            return [result for result in results
                    if isinstance(result, util.BadBinaryOperationMessage)]
        except exceptions.InferenceError:
            return []


@util.register_implementation(treeabc.Repr)
class Repr(base.NodeNG):
    """class representing a Repr node"""
    _astroid_fields = ('value',)
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.BinOp)
class BinOp(base.NodeNG):
    """class representing a BinOp node"""
    _astroid_fields = ('left', 'right')
    _other_fields = ('op',)
    left = None
    right = None

    def __init__(self, op=None, lineno=None, col_offset=None, parent=None):
        self.op = op
        super(BinOp, self).__init__(lineno, col_offset, parent)

    def postinit(self, left=None, right=None):
        self.left = left
        self.right = right

    def _infer_binop(self, context):
        return inference.infer_binop(self, nodes=sys.modules[__name__],
                                     context=context)

    def type_errors(self, context=None):
        """Return a list of TypeErrors which can occur during inference.

        Each TypeError is represented by a :class:`BadBinaryOperationMessage`,
        which holds the original exception.
        """
        try:
            results = self._infer_binop(context=context)
            return [result for result in results
                    if isinstance(result, util.BadBinaryOperationMessage)]
        except exceptions.InferenceError:
            return []


@util.register_implementation(treeabc.BoolOp)
class BoolOp(base.NodeNG):
    """class representing a BoolOp node"""
    _astroid_fields = ('values',)
    _other_fields = ('op',)
    values = None

    def __init__(self, op=None, lineno=None, col_offset=None, parent=None):
        self.op = op
        super(BoolOp, self).__init__(lineno, col_offset, parent)

    def postinit(self, values=None):
        self.values = values


@util.register_implementation(treeabc.Break)
class Break(Statement):
    """class representing a Break node"""


@util.register_implementation(treeabc.Call)
class Call(base.NodeNG):
    """class representing a Call node"""
    _astroid_fields = ('func', 'args', 'keywords')
    func = None
    args = None
    keywords = None

    def postinit(self, func=None, args=None, keywords=None):
        self.func = func
        self.args = args
        self.keywords = keywords

    @property
    def starargs(self):
        args = self.args or []
        return [arg for arg in args if isinstance(arg, Starred)]

    @property
    def kwargs(self):
        keywords = self.keywords or []
        return [keyword for keyword in keywords if keyword.arg is None]


@util.register_implementation(treeabc.Compare)
class Compare(base.NodeNG):
    """class representing a Compare node"""
    _astroid_fields = ('left', 'comparators')
    _other_fields = ('ops',)
    left = None

    def __init__(self, ops, lineno=None, col_offset=None, parent=None):
        self.comparators = []
        self.ops = ops
        super(Compare, self).__init__(lineno, col_offset, parent)

    def postinit(self, left=None, comparators=None):
        self.left = left
        self.comparators = comparators

    def get_children(self):
        """override get_children for tuple fields"""
        yield self.left
        for comparator in self.comparators:
            yield comparator

    def last_child(self):
        """override last_child"""
        return self.comparators[-1]


@util.register_implementation(treeabc.Comprehension)
class Comprehension(AssignedStmtsMixin, base.NodeNG):
    """class representing a Comprehension node"""
    _astroid_fields = ('target', 'iter', 'ifs')
    target = None
    iter = None
    ifs = None

    def __init__(self, parent=None):
        self.parent = parent

    def postinit(self, target=None, iter=None, ifs=None):
        self.target = target
        self.iter = iter
        self.ifs = ifs

    optional_assign = True
    def assign_type(self):
        return self

    def _get_filtered_stmts(self, lookup_node, node, stmts, mystmt):
        """method used in filter_stmts"""
        if self is mystmt:
            if isinstance(lookup_node, (Const, Name)):
                return [lookup_node], True

        elif self.statement() is mystmt:
            # original node's statement is the assignment, only keeps
            # current node (gen exp, list comp)

            return [node], True

        return stmts, False


@util.register_implementation(treeabc.Const)
@util.register_implementation(runtimeabc.BuiltinInstance)
class Const(base.NodeNG, objects.BaseInstance):
    """represent a constant node like num, str, bytes"""
    _other_fields = ('value',)

    def __init__(self, value, lineno=None, col_offset=None, parent=None):
        self.value = value
        super(Const, self).__init__(lineno, col_offset, parent)

    def getitem(self, index, context=None):
        if isinstance(index, Const):
            index_value = index.value
        elif isinstance(index, Slice):
            index_value = _infer_slice(index, context=context)
        else:
            raise TypeError(
                'Could not use type {} as subscript index'.format(type(index))
            )

        if isinstance(self.value, six.string_types):
            return Const(self.value[index_value])
        if isinstance(self.value, bytes) and six.PY3:
            # Bytes aren't instances of six.string_types
            # on Python 3. Also, indexing them should return
            # integers.
            return Const(self.value[index_value])

        raise TypeError('%r (value=%s)' % (self, self.value))

    def has_dynamic_getattr(self):
        return False

    def itered(self):
        if isinstance(self.value, six.string_types):
            return self.value
        raise TypeError()

    def pytype(self):
        return self._proxied.qname()

    def bool_value(self):
        return bool(self.value)

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr(type(self.value).__name__)[0]

    
@util.register_implementation(treeabc.NameConstant)
class NameConstant(Const):
    """Represents a builtin singleton, at the moment True, False, None,
    and NotImplemented.

    """

    # @decorators.cachedproperty
    # def _proxied(self):
    #     return self
    #     # builtins = MANAGER.builtins()
    #     # return builtins.getattr(str(self.value))[0]


@util.register_implementation(treeabc.ReservedName)
class ReservedName(base.NodeNG):
    '''Used in the builtins AST to assign names to singletons.'''
    _astroid_fields = ('value',)
    _other_fields = ('name',)

    def __init__(self, name, lineno=None, col_offset=None, parent=None):
        self.name = name
        super(ReservedName, self).__init__(lineno, col_offset, parent)

    def postinit(self, value):
        self.value = value


@util.register_implementation(treeabc.Continue)
class Continue(Statement):
    """class representing a Continue node"""


@util.register_implementation(treeabc.Decorators)
class Decorators(base.NodeNG):
    """class representing a Decorators node"""
    _astroid_fields = ('nodes',)
    nodes = None

    def postinit(self, nodes):
        self.nodes = nodes


@util.register_implementation(treeabc.DelAttr)
class DelAttr(base.ParentAssignTypeMixin, base.NodeNG):
    """class representing a DelAttr node"""
    _astroid_fields = ('expr',)
    _other_fields = ('attrname',)
    expr = None

    def __init__(self, attrname=None, lineno=None, col_offset=None, parent=None):
        self.attrname = attrname
        super(DelAttr, self).__init__(lineno, col_offset, parent)

    def postinit(self, expr=None):
        self.expr = expr


@util.register_implementation(treeabc.Delete)
class Delete(base.AssignTypeMixin, Statement):
    """class representing a Delete node"""
    _astroid_fields = ('targets',)
    targets = None

    def postinit(self, targets=None):
        self.targets = targets


@util.register_implementation(treeabc.Dict)
@util.register_implementation(runtimeabc.BuiltinInstance)
class Dict(base.NodeNG, objects.DictInstance):
    """class representing a Dict node"""
    _astroid_fields = ('keys', 'values')

    def __init__(self, lineno=None, col_offset=None, parent=None):
        self.keys = []
        self.values = []
        super(Dict, self).__init__(lineno, col_offset, parent)

    def postinit(self, keys, values):
        self.keys = keys
        self.values = values

    @property
    def items(self):
        return list(zip(self.keys, self.values))

    def pytype(self):
        return '%s.dict' % BUILTINS

    def get_children(self):
        """get children of a Dict node"""
        # overrides get_children
        for key, value in zip(self.keys, self.values):
            yield key
            yield value

    def last_child(self):
        """override last_child"""
        if self.values:
            return self.values[-1]
        return None

    def itered(self):
        return self.keys

    def getitem(self, lookup_key, context=None):
        for key, value in zip(self.keys, self.values):
            # TODO(cpopa): no support for overriding yet, {1:2, **{1: 3}}.
            if isinstance(key, DictUnpack):
                try:
                    return value.getitem(lookup_key, context)
                except IndexError:
                    continue
            for inferredkey in key.infer(context):
                if inferredkey is util.Uninferable:
                    continue
                if isinstance(inferredkey, Const) and isinstance(lookup_key, Const):
                    if inferredkey.value == lookup_key.value:
                        return value
        # This should raise KeyError, but all call sites only catch
        # IndexError. Let's leave it like that for now.
        raise IndexError(lookup_key)

    def bool_value(self):
        return bool(self.keys)

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr('dict')[0]


@util.register_implementation(treeabc.Expr)
class Expr(Statement):
    """class representing a Expr node"""
    _astroid_fields = ('value',)
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.Ellipsis)
class Ellipsis(base.NodeNG): # pylint: disable=redefined-builtin
    """class representing an Ellipsis node"""

    def bool_value(self):
        return True


_INTERPRETER_OBJECT_SENTINEL = object()


@util.register_implementation(treeabc.InterpreterObject)
class InterpreterObject(base.NodeNG):
    '''Used for connecting ASTs and runtime objects

    InterpreterObjects are used in manufactured ASTs that simulate features of
    real ASTs for inference, usually to handle behavior implemented in
    the interpreter or in C extensions. They can be used as a "translator"
    from a non-AST object, or in astroid's parlance, a runtime object
    to an AST. They mimick their underlying object, which means that an
    InterpreterObject can act as the object it is wrapping.
    '''
    _other_fields = ('name', 'object')
    object = _INTERPRETER_OBJECT_SENTINEL

    def __init__(self, object_=None, name=None, lineno=None, col_offset=None, parent=None):
        if object_ is not None:
            self.object = object_
        self.name = name
        super(InterpreterObject, self).__init__(lineno, col_offset, parent)

    def has_underlying_object(self):
        return self.object != _INTERPRETER_OBJECT_SENTINEL

    def __getattr__(self, attr):
        if self.has_underlying_object():
            return getattr(self.object, attr)
        raise AttributeError(attr)


@util.register_implementation(treeabc.ExceptHandler)
class ExceptHandler(base.AssignTypeMixin, AssignedStmtsMixin, Statement):
    """class representing an ExceptHandler node"""
    _astroid_fields = ('type', 'name', 'body',)
    type = None
    name = None
    body = None

    def postinit(self, type=None, name=None, body=None):
        self.type = type
        self.name = name
        self.body = body

    @decorators.cachedproperty
    def blockstart_tolineno(self):
        if self.name:
            return self.name.tolineno
        elif self.type:
            return self.type.tolineno
        else:
            return self.lineno

    def catch(self, exceptions):
        if self.type is None or exceptions is None:
            return True
        for node in self.type.nodes_of_class(Name):
            if node.name in exceptions:
                return True


@util.register_implementation(treeabc.Exec)
class Exec(Statement):
    """class representing an Exec node"""
    _astroid_fields = ('expr', 'globals', 'locals')
    expr = None
    globals = None
    locals = None

    def postinit(self, expr=None, globals=None, locals=None):
        self.expr = expr
        self.globals = globals
        self.locals = locals


@util.register_implementation(treeabc.ExtSlice)
class ExtSlice(base.NodeNG):
    """class representing an ExtSlice node"""
    _astroid_fields = ('dims',)
    dims = None

    def postinit(self, dims=None):
        self.dims = dims


@util.register_implementation(treeabc.For)
class For(base.BlockRangeMixIn, base.AssignTypeMixin,
          AssignedStmtsMixin, Statement):
    """class representing a For node"""
    _astroid_fields = ('target', 'iter', 'body', 'orelse',)
    target = None
    iter = None
    body = None
    orelse = None

    def postinit(self, target=None, iter=None, body=None, orelse=None):
        self.target = target
        self.iter = iter
        self.body = body
        self.orelse = orelse

    optional_assign = True
    @decorators.cachedproperty
    def blockstart_tolineno(self):
        return self.iter.tolineno


@util.register_implementation(treeabc.AsyncFor)
class AsyncFor(For):
    """Asynchronous For built with `async` keyword."""


@util.register_implementation(treeabc.Await)
class Await(base.NodeNG):
    """Await node for the `await` keyword."""

    _astroid_fields = ('value', )
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.ImportFrom)
class ImportFrom(base.FilterStmtsMixin, Statement):
    """class representing a ImportFrom node"""
    _other_fields = ('modname', 'names', 'level')

    def __init__(self, fromname, names, level=0, lineno=None,
                 col_offset=None, parent=None):
        self.modname = fromname
        self.names = names
        self.level = level
        super(ImportFrom, self).__init__(lineno, col_offset, parent)

    def _infer_name(self, frame, name):
        return name


@util.register_implementation(treeabc.Attribute)
class Attribute(base.NodeNG):
    """class representing a Attribute node"""
    _astroid_fields = ('expr',)
    _other_fields = ('attrname',)
    expr = None

    def __init__(self, attrname=None, lineno=None, col_offset=None, parent=None):
        self.attrname = attrname
        super(Attribute, self).__init__(lineno, col_offset, parent)

    def postinit(self, expr=None):
        self.expr = expr


@util.register_implementation(treeabc.Global)
class Global(Statement):
    """class representing a Global node"""
    _other_fields = ('names',)

    def __init__(self, names, lineno=None, col_offset=None, parent=None):
        self.names = names
        super(Global, self).__init__(lineno, col_offset, parent)

    def _infer_name(self, frame, name):
        return name


@util.register_implementation(treeabc.If)
class If(base.BlockRangeMixIn, Statement):
    """class representing an If node"""
    _astroid_fields = ('test', 'body', 'orelse')
    test = None
    body = None
    orelse = None

    def postinit(self, test=None, body=None, orelse=None):
        self.test = test
        self.body = body
        self.orelse = orelse

    @decorators.cachedproperty
    def blockstart_tolineno(self):
        return self.test.tolineno

    def block_range(self, lineno):
        """handle block line numbers range for if statements"""
        if lineno == self.body[0].fromlineno:
            return lineno, lineno
        if lineno <= self.body[-1].tolineno:
            return lineno, self.body[-1].tolineno
        return self._elsed_block_range(lineno, self.orelse,
                                       self.body[0].fromlineno - 1)


@util.register_implementation(treeabc.IfExp)
class IfExp(base.NodeNG):
    """class representing an IfExp node"""
    _astroid_fields = ('test', 'body', 'orelse')
    test = None
    body = None
    orelse = None

    def postinit(self, test=None, body=None, orelse=None):
        self.test = test
        self.body = body
        self.orelse = orelse


@util.register_implementation(treeabc.Import)
class Import(base.FilterStmtsMixin, Statement):
    """class representing an Import node"""
    _other_fields = ('names',)

    def __init__(self, names=None, lineno=None, col_offset=None, parent=None):
        self.names = names
        super(Import, self).__init__(lineno, col_offset, parent)

    def infer_name_module(self, name):
        context = contextmod.InferenceContext()
        context.lookupname = name
        return self.infer(context, asname=False)

    def _infer_name(self, frame, name):
        return name


@util.register_implementation(treeabc.Index)
class Index(base.NodeNG):
    """class representing an Index node"""
    _astroid_fields = ('value',)
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.Keyword)
class Keyword(base.NodeNG):
    """class representing a Keyword node"""
    _astroid_fields = ('value',)
    _other_fields = ('arg',)
    value = None

    def __init__(self, arg=None, lineno=None, col_offset=None, parent=None):
        self.arg = arg
        super(Keyword, self).__init__(lineno, col_offset, parent)

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.List)
@util.register_implementation(runtimeabc.BuiltinInstance)
class List(base.BaseContainer, AssignedStmtsMixin, objects.BaseInstance):
    """class representing a List node"""
    _other_fields = ('ctx',)

    def __init__(self, ctx=None, lineno=None,
                 col_offset=None, parent=None):
        self.ctx = ctx
        super(List, self).__init__(lineno, col_offset, parent)

    def pytype(self):
        return '%s.list' % BUILTINS

    def getitem(self, index, context=None):
        return _container_getitem(self, self.elts, index)

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr('list')[0]


@util.register_implementation(treeabc.Nonlocal)
class Nonlocal(Statement):
    """class representing a Nonlocal node"""
    _other_fields = ('names',)

    def __init__(self, names, lineno=None, col_offset=None, parent=None):
        self.names = names
        super(Nonlocal, self).__init__(lineno, col_offset, parent)

    def _infer_name(self, frame, name):
        return name


@util.register_implementation(treeabc.Pass)
class Pass(Statement):
    """class representing a Pass node"""


@util.register_implementation(treeabc.Print)
class Print(Statement):
    """class representing a Print node"""
    _astroid_fields = ('dest', 'values',)
    dest = None
    values = None

    def __init__(self, nl=None, lineno=None, col_offset=None, parent=None):
        self.nl = nl
        super(Print, self).__init__(lineno, col_offset, parent)

    def postinit(self, dest=None, values=None):
        self.dest = dest
        self.values = values


@util.register_implementation(treeabc.Raise)
class Raise(Statement):
    """class representing a Raise node"""
    exc = None
    if six.PY2:
        _astroid_fields = ('exc', 'inst', 'tback')
        inst = None
        tback = None

        def postinit(self, exc=None, inst=None, tback=None):
            self.exc = exc
            self.inst = inst
            self.tback = tback
    else:
        _astroid_fields = ('exc', 'cause')
        exc = None
        cause = None

        def postinit(self, exc=None, cause=None):
            self.exc = exc
            self.cause = cause

    def raises_not_implemented(self):
        if not self.exc:
            return
        for name in self.exc.nodes_of_class(Name):
            if name.name == 'NotImplementedError':
                return True


@util.register_implementation(treeabc.Return)
class Return(Statement):
    """class representing a Return node"""
    _astroid_fields = ('value',)
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.Set)
@util.register_implementation(runtimeabc.BuiltinInstance)
class Set(base.BaseContainer, objects.BaseInstance):
    """class representing a Set node"""

    def pytype(self):
        return '%s.set' % BUILTINS

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr('set')[0]


@util.register_implementation(treeabc.Slice)
class Slice(base.NodeNG):
    """class representing a Slice node"""
    _astroid_fields = ('lower', 'upper', 'step')
    lower = None
    upper = None
    step = None

    def postinit(self, lower=None, upper=None, step=None):
        self.lower = lower
        self.upper = upper
        self.step = step

    def _wrap_attribute(self, attr):
        """Wrap the empty attributes of the Slice in a Const node."""
        if not attr:
            return Const(attr, parent=self)
        return attr

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr('slice')[0]

    def pytype(self):
        return '%s.slice' % BUILTINS

    def igetattr(self, attrname, context=None):
        if attrname == 'start':
            yield self._wrap_attribute(self.lower)
        elif attrname == 'stop':
            yield self._wrap_attribute(self.upper)
        elif attrname == 'step':
            yield self._wrap_attribute(self.step)
        else:
            for value in self.getattr(attrname, context=context):
                yield value

    def getattr(self, attrname, context=None):
        return self._proxied.getattr(attrname, context)


@util.register_implementation(treeabc.Starred)
class Starred(base.ParentAssignTypeMixin, AssignedStmtsMixin, base.NodeNG):
    """class representing a Starred node"""
    _astroid_fields = ('value',)
    _other_fields = ('ctx', )
    value = None

    def __init__(self, ctx=None, lineno=None, col_offset=None, parent=None):
        self.ctx = ctx
        super(Starred, self).__init__(lineno=lineno,
                                      col_offset=col_offset, parent=parent)

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.Subscript)
class Subscript(base.NodeNG):
    """class representing a Subscript node"""
    _astroid_fields = ('value', 'slice')
    _other_fields = ('ctx', )
    value = None
    slice = None

    def __init__(self, ctx=None, lineno=None, col_offset=None, parent=None):
        self.ctx = ctx
        super(Subscript, self).__init__(lineno=lineno,
                                        col_offset=col_offset, parent=parent)

    def postinit(self, value=None, slice=None):
        self.value = value
        self.slice = slice

    infer_lhs = inference.infer_subscript


@util.register_implementation(treeabc.TryExcept)
class TryExcept(base.BlockRangeMixIn, Statement):
    """class representing a TryExcept node"""
    _astroid_fields = ('body', 'handlers', 'orelse',)
    body = None
    handlers = None
    orelse = None

    def postinit(self, body=None, handlers=None, orelse=None):
        self.body = body
        self.handlers = handlers
        self.orelse = orelse

    def _infer_name(self, frame, name):
        return name

    def block_range(self, lineno):
        """handle block line numbers range for try/except statements"""
        last = None
        for exhandler in self.handlers:
            if exhandler.type and lineno == exhandler.type.fromlineno:
                return lineno, lineno
            if exhandler.body[0].fromlineno <= lineno <= exhandler.body[-1].tolineno:
                return lineno, exhandler.body[-1].tolineno
            if last is None:
                last = exhandler.body[0].fromlineno - 1
        return self._elsed_block_range(lineno, self.orelse, last)


@util.register_implementation(treeabc.TryFinally)
class TryFinally(base.BlockRangeMixIn, Statement):
    """class representing a TryFinally node"""
    _astroid_fields = ('body', 'finalbody',)
    body = None
    finalbody = None

    def postinit(self, body=None, finalbody=None):
        self.body = body
        self.finalbody = finalbody

    def block_range(self, lineno):
        """handle block line numbers range for try/finally statements"""
        child = self.body[0]
        # py2.5 try: except: finally:
        if (isinstance(child, TryExcept) and child.fromlineno == self.fromlineno
                and lineno > self.fromlineno and lineno <= child.tolineno):
            return child.block_range(lineno)
        return self._elsed_block_range(lineno, self.finalbody)


@util.register_implementation(treeabc.Tuple)
@util.register_implementation(runtimeabc.BuiltinInstance)
class Tuple(base.BaseContainer, AssignedStmtsMixin, objects.BaseInstance):
    """class representing a Tuple node"""

    _other_fields = ('ctx',)

    def __init__(self, ctx=None, lineno=None,
                 col_offset=None, parent=None):
        self.ctx = ctx
        super(Tuple, self).__init__(lineno, col_offset, parent)

    def pytype(self):
        return '%s.tuple' % BUILTINS

    def getitem(self, index, context=None):
        return _container_getitem(self, self.elts, index)

    @decorators.cachedproperty
    def _proxied(self):
        builtins = MANAGER.builtins()
        return builtins.getattr('tuple')[0]


@util.register_implementation(treeabc.UnaryOp)
class UnaryOp(base.NodeNG):
    """class representing an UnaryOp node"""
    _astroid_fields = ('operand',)
    _other_fields = ('op',)
    operand = None

    def __init__(self, op=None, lineno=None, col_offset=None, parent=None):
        self.op = op
        super(UnaryOp, self).__init__(lineno, col_offset, parent)

    def postinit(self, operand=None):
        self.operand = operand

    def _infer_unaryop(self, context=None):
        return inference.infer_unaryop(self, nodes=sys.modules[__name__],
                                       context=context)

    def type_errors(self, context=None):
        """Return a list of TypeErrors which can occur during inference.

        Each TypeError is represented by a :class:`BadUnaryOperationMessage`,
        which holds the original exception.
        """
        try:
            results = self._infer_unaryop(context=context)
            return [result for result in results
                    if isinstance(result, util.BadUnaryOperationMessage)]
        except exceptions.InferenceError:
            return []


@util.register_implementation(treeabc.While)
class While(base.BlockRangeMixIn, Statement):
    """class representing a While node"""
    _astroid_fields = ('test', 'body', 'orelse',)
    test = None
    body = None
    orelse = None

    def postinit(self, test=None, body=None, orelse=None):
        self.test = test
        self.body = body
        self.orelse = orelse

    @decorators.cachedproperty
    def blockstart_tolineno(self):
        return self.test.tolineno

    def block_range(self, lineno):
        """handle block line numbers range for for and while statements"""
        return self. _elsed_block_range(lineno, self.orelse)


@util.register_implementation(treeabc.With)
class With(base.BlockRangeMixIn, base.AssignTypeMixin,
           AssignedStmtsMixin, Statement):
    """class representing a With node"""
    _astroid_fields = ('items', 'body')

    def __init__(self, lineno=None, col_offset=None, parent=None):
        self.items = []
        self.body = []
        super(With, self).__init__(lineno, col_offset, parent)

    def postinit(self, items=None, body=None):
        self.items = items
        self.body = body

    @decorators.cachedproperty
    def blockstart_tolineno(self):
        return self.items[-1].context_expr.tolineno


@util.register_implementation(treeabc.WithItem)
class WithItem(base.ParentAssignTypeMixin, AssignedStmtsMixin, base.NodeNG):
    _astroid_fields = ('context_expr', 'optional_vars')
    context_expr = None
    optional_vars = None

    def postinit(self, context_expr=None, optional_vars=None):
        self.context_expr = context_expr
        self.optional_vars = optional_vars


@util.register_implementation(treeabc.AsyncWith)
class AsyncWith(With):
    """Asynchronous `with` built with the `async` keyword."""


@util.register_implementation(treeabc.Yield)
class Yield(base.NodeNG):
    """class representing a Yield node"""
    _astroid_fields = ('value',)
    value = None

    def postinit(self, value=None):
        self.value = value


@util.register_implementation(treeabc.YieldFrom)
class YieldFrom(Yield):
    """ Class representing a YieldFrom node. """


@util.register_implementation(treeabc.DictUnpack)
class DictUnpack(base.NodeNG):
    """Represents the unpacking of dicts into dicts using PEP 448."""


@object.__new__
@util.register_implementation(treeabc.Empty)
class Empty(base.NodeNG):
    """Empty nodes represents the lack of something

    For instance, they can be used to represent missing annotations
    or defaults for arguments or anything where None is a valid
    value.
    """

    def __bool__(self):
        return False

    __nonzero__ = __bool__


# Register additional inference dispatched functions. We do
# this here, since we need to pass this module as an argument
# to these functions, in order to avoid circular dependencies
# between inference and node_classes.

_module = sys.modules[__name__]
inference.infer.register(treeabc.UnaryOp,
                         functools.partial(inference.filtered_infer_unaryop,
                                           nodes=_module))
inference.infer.register(treeabc.Arguments,
                         functools.partial(inference.infer_arguments,
                                           nodes=_module))
inference.infer.register(treeabc.BinOp,
                         functools.partial(inference.filtered_infer_binop,
                                           nodes=_module))
inference.infer.register(treeabc.AugAssign,
                         functools.partial(inference.filtered_infer_augassign,
                                           nodes=_module))
del _module