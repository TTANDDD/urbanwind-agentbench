"""Read the explicit OpenFOAM dictionaries used by the benchmark.

Whitespace, comments and quoted names are lexical, not physical differences.
This parser never executes includes, substitutions, codeStream or directives.
Unsupported input is reported rather than accepted by a substring coincidence.
"""
from __future__ import annotations
import math
import re

TOKEN = re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|[{}();\[\]]|[^\s{}();\[\]"]+')


def parse_dictionary(text):
    tokens=[]
    end=0
    for match in TOKEN.finditer(text):
        if match.start()!=end:
            raise ValueError('unsupported_dictionary_syntax')
        end=match.end();value=match.group()
        if value.isspace() or value.startswith(('//','/*')):continue
        if value.startswith('#'):
            raise ValueError('dictionary_directive_requires_resolution')
        tokens.append(value[1:-1] if value.startswith('"') else value)
    if end!=len(text):raise ValueError('unterminated_dictionary_token')
    index=0
    def block(nested=False):
        nonlocal index
        result={}
        while index<len(tokens):
            key=tokens[index];index+=1
            if key=='}':
                if not nested:raise ValueError('unexpected_dictionary_close')
                return result
            if key==';':continue
            if key in ('{','(',')','[',']') or key in result:
                raise ValueError('invalid_or_duplicate_dictionary_key:'+key)
            if index>=len(tokens):raise ValueError('missing_dictionary_value:'+key)
            if tokens[index]=='{':
                index+=1;result[key]=block(True);continue
            value=[];stack=[]
            while index<len(tokens):
                token=tokens[index];index+=1
                if token==';' and not stack:break
                if token in ('(','[') or (token=='{' and stack):stack.append(token)
                elif token in (')',']') or (token=='}' and stack):
                    if not stack or stack.pop()!= {')':'(',']':'[','}':'{'}[token]:
                        raise ValueError('unbalanced_dictionary_list')
                elif token in ('{','}'):
                    raise ValueError('unsupported_dictionary_value_block')
                value.append(token)
            else:raise ValueError('missing_dictionary_semicolon:'+key)
            if stack:raise ValueError('unclosed_dictionary_list')
            result[key]=' '.join(value)
        if nested:raise ValueError('unclosed_dictionary_block')
        return result
    return block()


def entry(text, *path):
    value=parse_dictionary(text)
    for key in path:
        if not isinstance(value,dict):return None
        value=value.get(key)
    return value


def matches(text, path, expected):
    actual=entry(text,*path)
    if isinstance(expected,(int,float)):
        try:return math.isfinite(float(actual)) and float(actual)==expected
        except (TypeError,ValueError):return False
    return actual==expected


def boundary_type(text, patch, kind):
    return matches(text,('boundaryField',patch,'type'),kind)


def uniform_number(text, patch, field, value):
    actual=entry(text,'boundaryField',patch,field)
    if not isinstance(actual,str):return False
    tokens=actual.split()
    if len(tokens)!=2 or tokens[0]!='uniform':return False
    try:return math.isfinite(float(tokens[1])) and float(tokens[1])==value
    except ValueError:return False


def function_setting(text, name, key, expected):
    return matches(text,('functions',name,key),expected)
