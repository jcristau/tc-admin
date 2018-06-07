# -*- coding: utf-8 -*-

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

import attr
import blessings
import functools
import textwrap
import itertools
import difflib
import re
from memoized import memoized
from sortedcontainers import SortedKeyList

from .util import strip_ansi
from ..util import (
    MatchList,
    pretty_json,
)

t = blessings.Terminal()


@functools.total_ordering
@attr.s(frozen=True, slots=True)
class Resource(object):
    '''
    Base class for a single runtime configuration resource
    '''

    @classmethod
    @memoized
    def _kind_classes(cls):
        return dict((c.__name__, c) for c in cls.__subclasses__())

    @classmethod
    def from_json(cls, json):
        '''
        Given a kind and the result of to_json, create a new object

        Note that this modifies the given value in-place
        '''
        kind = json.pop('kind')
        return cls._kind_classes()[kind](**json)

    def to_json(self):
        'Return a JSON-able version of this object, including a `kind` property'
        d = attr.asdict(self)
        d['kind'] = self.kind
        return d

    @property
    def kind(self):
        'The kind of this instance'
        return self.__class__.__name__

    @property
    def id(self):
        'The id of this instance, including the kind name'
        return '{}={}'.format(self.kind, getattr(self, attr.fields(self.__class__)[0].name))

    def evolve(self, **args):
        'Create a new resource like this one, but with the named attributes replaced'
        return attr.evolve(self, **args)

    def __str__(self):
        rv = ['{t.underline}{id}{t.normal}:'.format(t=t, id=self.id)]
        for a in attr.fields(self.__class__):
            label = '  {t.bold}{a.name}{t.normal}:'.format(t=t, a=a)
            formatted = a.metadata.get('formatter', str)(getattr(self, a.name))
            if '\n' in formatted:
                rv.append(label)
                rv.append(textwrap.indent(formatted, '    '))
            else:
                rv.append('{} {}'.format(label, formatted))
        return '\n'.join(rv)


@attr.s(repr=False)
class Resources:
    '''
    Container class for multiple resource instances.

    This class also tracks what resources are "managed", allowing deletion of
    resources that are no longer defined.
    '''

    resources = attr.ib(
        type=SortedKeyList,
        converter=lambda resources: SortedKeyList(resources, key=lambda r: r.id),
        default=[])
    managed = attr.ib(
        type=MatchList,
        converter=lambda managed: MatchList(managed),
        default=MatchList([]))

    def __attrs_post_init__(self):
        self._verify()

    def add(self, resource):
        'Add the given resource to the collection'
        if not self.is_managed(resource.id):
            raise RuntimeError('unmanaged resource: ' + resource.id)
        self.resources.add(resource)

    def manage(self, pattern):
        'Add the given pattern to the list of managed resources'
        self.managed.add(pattern)

    def _verify(self):
        'Verify that this set of resources is legal (all managed, no duplicates)'

        # search for duplicates, taking advantage of sorting
        pairs = zip(itertools.chain([None], (r1.id for r1 in self)), (r2.id for r2 in self))
        dupes = [a for (a, b) in pairs if a == b]
        if dupes:
            unique_dupes = sorted(set(dupes))
            raise RuntimeError('duplicate resources: ' + ', '.join(unique_dupes))

        unmanaged = sorted([r.id for r in self if not self.is_managed(r.id)])
        if unmanaged:
            raise RuntimeError('unmanaged resources: ' + ', '.join(unmanaged))

    def is_managed(self, id):
        'Return True if the given id is managed'
        return self.managed.matches(id)

    def __iter__(self):
        return self.resources.__iter__()

    def __str__(self):
        self._verify()
        return 'managed:\n{}\n\nresources:\n{}'.format(
            '\n'.join('  - ' + m for m in self.managed),
            textwrap.indent('\n\n'.join(str(r) for r in self), '  '),
        )

    def __repr__(self):
        return pretty_json(self.to_json())

    def to_json(self):
        'Convert to a JSON-able data structure'
        self._verify()
        return {
            'resources': [r.to_json() for r in self],
            'managed': list(self.managed),
        }

    @classmethod
    def from_json(cls, json):
        return Resources(
            (Resource.from_json(r) for r in json['resources']),
            json['managed'])

    def diff(self, other, **kwargs):
        '''
        Compare changes from other to self, returning a string.

        kwargs are passed to difflib.unified_diff
        '''
        left = str(other).split('\n')
        right = str(self).split('\n')
        resources_start = left.index('resources:')
        context_re = re.compile(r'^@@ -([0-9]*),')
        label_re = re.compile(r'^  ([^ ].*)')  # lines with exactly two spaces indentation

        def contextualize(rangeInfo):
            'add context information to range (@@ .. @@) line'
            match = context_re.match(rangeInfo)
            if not match:
                return ''
            line = int(match.group(1))
            while line > resources_start:
                line -= 1
                match = label_re.match(left[line])
                if match:
                    return match.group(1)
            return ''

        lines = difflib.unified_diff(left, right, lineterm='', **kwargs)
        colors = {
            '-': lambda s: t.red(strip_ansi(s)),
            '+': lambda s: t.green(strip_ansi(s)),
            '@': lambda s: t.yellow(strip_ansi(s)) + ' ' + contextualize(s),
            ' ': lambda s: s,
        }
        # colorize the lines
        lines = (colors[l[0]](l).rstrip() for l in (line if line else ' ' for line in lines))
        return '\n'.join(lines)