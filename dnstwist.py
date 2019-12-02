#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#      _           _            _     _
#   __| |_ __  ___| |___      _(_)___| |_
#  / _` | '_ \/ __| __\ \ /\ / / / __| __|
# | (_| | | | \__ \ |_ \ V  V /| \__ \ |_
#  \__,_|_| |_|___/\__| \_/\_/ |_|___/\__|
#
# Generate and resolve domain variations to detect typo squatting,
# phishing and corporate espionage.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

__author__ = "Marcin Ulikowski"
__version__ = "20190706"
__email__ = "marcin@ulikowski.pl"

import abc
import asyncdns
import argparse
import asyncio
import collections
import datetime
import idna
import json
import pathlib
import re
import sys
from random import randint

# import signal
# from os import path
# import socket
# import smtplib
# import GeoIP
# import whois
# import ssdeep

DIR = pathlib.Path(sys.argv[0]).parent
DIR_DB = "database"
# FILE_GEOIP = path.join(DIR, DIR_DB, "GeoIP.dat")
FILE_TLD = pathlib.Path(DIR, DIR_DB, "effective_tld_names.dat")

# DB_GEOIP = FILE_GEOIP.exists()

asyncdns.resolver.TIMEOUT = 5

REQUEST_TIMEOUT_HTTP = 5
REQUEST_TIMEOUT_SMTP = 5

if sys.platform != "win32" and sys.stdout.isatty():
    FG_RND = "\x1b[3%dm" % randint(1, 8)
    FG_RED = "\x1b[31m"
    FG_YEL = "\x1b[33m"
    FG_GRE = "\x1b[32m"
    FG_MAG = "\x1b[35m"
    FG_CYA = "\x1b[36m"
    FG_BLU = "\x1b[34m"
    FG_RST = "\x1b[39m"
    ST_BRI = "\x1b[1m"
    ST_RST = "\x1b[0m"
else:
    FG_RND = ""
    FG_RED = ""
    FG_YEL = ""
    FG_GRE = ""
    FG_MAG = ""
    FG_CYA = ""
    FG_BLU = ""
    FG_RST = ""
    ST_BRI = ""
    ST_RST = ""


# XXX Revisit this since it's not async
def p_err(data, status=None):
    sys.stderr.write(path.basename(sys.argv[0]) + ": " + data)
    sys.stderr.flush()
    if status is not None:
        sys.exit(status)


def bye(code):
    sys.stdout.write(FG_RST + ST_RST)
    sys.exit(code)


# def sigint_handler(signal, frame):
#    sys.stdout.write('\nStopping threads... ')
#    sys.stdout.flush()
#    for worker in threads:
#        worker.stop()
#        worker.join()
#    sys.stdout.write('Done\n')
#    bye(0)


class UrlParser:
    # XXX Can we just use urllib.parse?

    def __init__(self, url):
        if "://" not in url:
            self.url = "http://" + url
        else:
            self.url = url
        self.scheme = ""
        self.authority = ""
        self.domain = ""
        self.path = ""
        self.query = ""

        self.__parse()

    def __parse(self):
        re_rfc3986_enhanced = re.compile(
            r"""
        ^
        (?:(?P<scheme>[^:/?#\s]+):)?
        (?://(?P<authority>[^/?#\s]*))?
        (?P<path>[^?#\s]*)
        (?:\?(?P<query>[^#\s]*))?
        (?:\#(?P<fragment>[^\s]*))?
        $
        """,
            re.MULTILINE | re.VERBOSE,
        )

        m_uri = re_rfc3986_enhanced.match(self.url)

        if m_uri:
            if m_uri.group("scheme"):
                if m_uri.group("scheme").startswith("http"):
                    self.scheme = m_uri.group("scheme")
                else:
                    self.scheme = "http"
            if m_uri.group("authority"):
                self.authority = m_uri.group("authority")
                self.domain = self.authority.split(":")[0].lower()
                if not self.__validate_domain(self.domain):
                    raise ValueError("Invalid domain name.")
            if m_uri.group("path"):
                self.path = m_uri.group("path")
            if m_uri.group("query"):
                if len(m_uri.group("query")):
                    self.query = "?" + m_uri.group("query")

    def __validate_domain(self, domain):
        if len(domain) > 255:
            return False
        if domain[-1] == ".":
            domain = domain[:-1]
        allowed = re.compile(r"\A([a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}\Z", re.IGNORECASE)
        return allowed.match(domain)

    def get_full_uri(self):
        return self.scheme + "://" + self.domain + self.path + self.query


# XXX This seems a little weird.
def parse_effective_tld_names():
    cc_tld = collections.defaultdict(list)
    re_tld = re.compile(r"^[a-z]{2,4}\.[a-z]{2}$", re.IGNORECASE)
    with open(FILE_TLD, "rt") as fil:
        for line in fil:
            line = line.strip()
            if re_tld.match(line):
                sld, tld = line.split(".")
                cc_tld[tld].append(sld)

    return cc_tld


class DomainGenerator(metaclass=abc.ABCMeta):
    cc_tld = parse_effective_tld_names()

    def __init__(self, domain):
        self.domain, self.tld = self.domain_tld(domain)
        self.domains = collections.deque()

    def domain_tld(self, domain):
        domain = domain.rsplit(".", 2)

        if len(domain) == 2:
            return domain[0], domain[1]

        sld_tld = self.cc_tld.get(domain[2])
        if sld_tld:
            if domain[1] in sld_tld:
                return domain[0], domain[1] + "." + domain[2]

        return domain[0] + "." + domain[1], domain[2]

    @abc.abstractmethod
    def generate(self):
        return


class DomainFuzz(DomainGenerator):
    def __init__(self, domain):
        super().__init__(domain)
        self.qwerty = {
            "1": "2q",
            "2": "3wq1",
            "3": "4ew2",
            "4": "5re3",
            "5": "6tr4",
            "6": "7yt5",
            "7": "8uy6",
            "8": "9iu7",
            "9": "0oi8",
            "0": "po9",
            "q": "12wa",
            "w": "3esaq2",
            "e": "4rdsw3",
            "r": "5tfde4",
            "t": "6ygfr5",
            "y": "7uhgt6",
            "u": "8ijhy7",
            "i": "9okju8",
            "o": "0plki9",
            "p": "lo0",
            "a": "qwsz",
            "s": "edxzaw",
            "d": "rfcxse",
            "f": "tgvcdr",
            "g": "yhbvft",
            "h": "ujnbgy",
            "j": "ikmnhu",
            "k": "olmji",
            "l": "kop",
            "z": "asx",
            "x": "zsdc",
            "c": "xdfv",
            "v": "cfgb",
            "b": "vghn",
            "n": "bhjm",
            "m": "njk",
        }
        self.qwertz = {
            "1": "2q",
            "2": "3wq1",
            "3": "4ew2",
            "4": "5re3",
            "5": "6tr4",
            "6": "7zt5",
            "7": "8uz6",
            "8": "9iu7",
            "9": "0oi8",
            "0": "po9",
            "q": "12wa",
            "w": "3esaq2",
            "e": "4rdsw3",
            "r": "5tfde4",
            "t": "6zgfr5",
            "z": "7uhgt6",
            "u": "8ijhz7",
            "i": "9okju8",
            "o": "0plki9",
            "p": "lo0",
            "a": "qwsy",
            "s": "edxyaw",
            "d": "rfcxse",
            "f": "tgvcdr",
            "g": "zhbvft",
            "h": "ujnbgz",
            "j": "ikmnhu",
            "k": "olmji",
            "l": "kop",
            "y": "asx",
            "x": "ysdc",
            "c": "xdfv",
            "v": "cfgb",
            "b": "vghn",
            "n": "bhjm",
            "m": "njk",
        }
        self.azerty = {
            "1": "2a",
            "2": "3za1",
            "3": "4ez2",
            "4": "5re3",
            "5": "6tr4",
            "6": "7yt5",
            "7": "8uy6",
            "8": "9iu7",
            "9": "0oi8",
            "0": "po9",
            "a": "2zq1",
            "z": "3esqa2",
            "e": "4rdsz3",
            "r": "5tfde4",
            "t": "6ygfr5",
            "y": "7uhgt6",
            "u": "8ijhy7",
            "i": "9okju8",
            "o": "0plki9",
            "p": "lo0m",
            "q": "zswa",
            "s": "edxwqz",
            "d": "rfcxse",
            "f": "tgvcdr",
            "g": "yhbvft",
            "h": "ujnbgy",
            "j": "iknhu",
            "k": "olji",
            "l": "kopm",
            "m": "lp",
            "w": "sxq",
            "x": "wsdc",
            "c": "xdfv",
            "v": "cfgb",
            "b": "vghn",
            "n": "bhj",
        }
        self.keyboards = [self.qwerty, self.qwertz, self.azerty]

    def __validate_domain(self, domain):
        try:
            domain_idna = idna.encode(domain).decode()
        except UnicodeError:
            # '.tla'.encode('idna') raises UnicodeError: label empty or too long
            # This can be obtained when __omission takes a one-letter domain.
            return False
        if len(domain) == len(domain_idna) and domain != domain_idna:
            return False
        allowed = re.compile(
            r"(?=^.{4,253}$)(^((?!-)[a-zA-Z0-9-]{1,63}(?<!-)\.)+[a-zA-Z]{2,63}\.?$)",
            re.IGNORECASE,
        )
        return allowed.match(domain_idna)

    def __filter_domains(self):
        seen = set()
        filtered = collections.deque()

        for d in self.domains:
            # if not self.__validate_domain(d['domain-name']):
            # p_err("debug: invalid domain %s\n" % d['domain-name'])
            if (
                self.__validate_domain(d["domain-name"])
                and d["domain-name"] not in seen
            ):
                seen.add(d["domain-name"])
                filtered.append(d)

        self.domains = filtered

    def __bitsquatting(self):
        result = []
        masks = [1, 2, 4, 8, 16, 32, 64, 128]
        for i in range(0, len(self.domain)):
            c = self.domain[i]
            for j in range(0, len(masks)):
                b = chr(ord(c) ^ masks[j])
                o = ord(b)
                if (o >= 48 and o <= 57) or (o >= 97 and o <= 122) or o == 45:
                    result.append(self.domain[:i] + b + self.domain[i + 1 :])

        return result

    def __homoglyph(self):
        glyphs = {
            "a": ["à", "á", "â", "ã", "ä", "å", "ɑ", "ạ", "ǎ", "ă", "ȧ", "ą"],
            "b": ["d", "lb", "ʙ", "ɓ", "ḃ", "ḅ", "ḇ", "ƅ"],
            "c": ["e", "ƈ", "ċ", "ć", "ç", "č", "ĉ"],
            "d": ["b", "cl", "dl", "ɗ", "đ", "ď", "ɖ", "ḑ", "ḋ", "ḍ", "ḏ", "ḓ"],
            "e": ["c", "é", "è", "ê", "ë", "ē", "ĕ", "ě", "ė", "ẹ", "ę", "ȩ", "ɇ", "ḛ"],
            "f": ["ƒ", "ḟ"],
            "g": ["q", "ɢ", "ɡ", "ġ", "ğ", "ǵ", "ģ", "ĝ", "ǧ", "ǥ"],
            "h": ["lh", "ĥ", "ȟ", "ħ", "ɦ", "ḧ", "ḩ", "ⱨ", "ḣ", "ḥ", "ḫ", "ẖ"],
            "i": ["1", "l", "í", "ì", "ï", "ı", "ɩ", "ǐ", "ĭ", "ỉ", "ị", "ɨ", "ȋ", "ī"],
            "j": ["ʝ", "ɉ"],
            "k": ["lk", "ik", "lc", "ḳ", "ḵ", "ⱪ", "ķ"],
            "l": ["1", "i", "ɫ", "ł"],
            "m": ["n", "nn", "rn", "rr", "ṁ", "ṃ", "ᴍ", "ɱ", "ḿ"],
            "n": ["m", "r", "ń", "ṅ", "ṇ", "ṉ", "ñ", "ņ", "ǹ", "ň", "ꞑ"],
            "o": ["0", "ȯ", "ọ", "ỏ", "ơ", "ó", "ö"],
            "p": ["ƿ", "ƥ", "ṕ", "ṗ"],
            "q": ["g", "ʠ"],
            "r": ["ʀ", "ɼ", "ɽ", "ŕ", "ŗ", "ř", "ɍ", "ɾ", "ȓ", "ȑ", "ṙ", "ṛ", "ṟ"],
            "s": ["ʂ", "ś", "ṣ", "ṡ", "ș", "ŝ", "š"],
            "t": ["ţ", "ŧ", "ṫ", "ṭ", "ț", "ƫ"],
            "u": [
                "ᴜ",
                "ǔ",
                "ŭ",
                "ü",
                "ʉ",
                "ù",
                "ú",
                "û",
                "ũ",
                "ū",
                "ų",
                "ư",
                "ů",
                "ű",
                "ȕ",
                "ȗ",
                "ụ",
            ],
            "v": ["ṿ", "ⱱ", "ᶌ", "ṽ", "ⱴ"],
            "w": ["vv", "ŵ", "ẁ", "ẃ", "ẅ", "ⱳ", "ẇ", "ẉ", "ẘ"],
            "y": ["ʏ", "ý", "ÿ", "ŷ", "ƴ", "ȳ", "ɏ", "ỿ", "ẏ", "ỵ"],
            "z": ["ʐ", "ż", "ź", "ᴢ", "ƶ", "ẓ", "ẕ", "ⱬ"],
        }

        result_1pass = set()

        for ws in range(1, len(self.domain)):
            for i in range(0, (len(self.domain) - ws) + 1):
                win = self.domain[i : i + ws]
                j = 0
                while j < ws:
                    c = win[j]
                    if c in glyphs:
                        win_copy = win
                        for g in glyphs[c]:
                            win = win.replace(c, g)
                            result_1pass.add(
                                self.domain[:i] + win + self.domain[i + ws :]
                            )
                            win = win_copy
                    j += 1

        result_2pass = set()

        for domain in result_1pass:
            for ws in range(1, len(domain)):
                for i in range(0, (len(domain) - ws) + 1):
                    win = domain[i : i + ws]
                    j = 0
                    while j < ws:
                        c = win[j]
                        if c in glyphs:
                            win_copy = win
                            for g in glyphs[c]:
                                win = win.replace(c, g)
                                result_2pass.add(domain[:i] + win + domain[i + ws :])
                                win = win_copy
                        j += 1

        return list(result_1pass | result_2pass)

    def __hyphenation(self):
        result = []

        for i in range(1, len(self.domain)):
            result.append(self.domain[:i] + "-" + self.domain[i:])

        return result

    def __insertion(self):
        result = []

        for i in range(1, len(self.domain) - 1):
            for keys in self.keyboards:
                if self.domain[i] in keys:
                    for c in keys[self.domain[i]]:
                        result.append(
                            self.domain[:i] + c + self.domain[i] + self.domain[i + 1 :]
                        )
                        result.append(
                            self.domain[:i] + self.domain[i] + c + self.domain[i + 1 :]
                        )

        return list(set(result))

    def __omission(self):
        result = []

        for i in range(0, len(self.domain)):
            result.append(self.domain[:i] + self.domain[i + 1 :])

        n = re.sub(r"(.)\1+", r"\1", self.domain)

        if n not in result and n != self.domain:
            result.append(n)

        return list(set(result))

    def __repetition(self):
        result = []

        for i in range(0, len(self.domain)):
            if self.domain[i].isalpha():
                result.append(
                    self.domain[:i]
                    + self.domain[i]
                    + self.domain[i]
                    + self.domain[i + 1 :]
                )

        return list(set(result))

    def __replacement(self):
        result = []

        for i in range(0, len(self.domain)):
            for keys in self.keyboards:
                if self.domain[i] in keys:
                    for c in keys[self.domain[i]]:
                        result.append(self.domain[:i] + c + self.domain[i + 1 :])

        return list(set(result))

    def __subdomain(self):
        result = []

        for i in range(1, len(self.domain)):
            if self.domain[i] not in ["-", "."] and self.domain[i - 1] not in [
                "-",
                ".",
            ]:
                result.append(self.domain[:i] + "." + self.domain[i:])

        return result

    def __transposition(self):
        result = []

        for i in range(0, len(self.domain) - 1):
            if self.domain[i + 1] != self.domain[i]:
                result.append(
                    self.domain[:i]
                    + self.domain[i + 1]
                    + self.domain[i]
                    + self.domain[i + 2 :]
                )

        return result

    def __vowel_swap(self):
        vowels = "aeiou"
        result = []

        for i in range(0, len(self.domain)):
            for vowel in vowels:
                if self.domain[i] in vowels:
                    result.append(self.domain[:i] + vowel + self.domain[i + 1 :])

        return list(set(result))

    def __addition(self):
        result = []

        for i in range(97, 123):
            result.append(self.domain + chr(i))

        return result

    # XXX It seems like this could use a generator but then we'd still have to keep
    # track of which ones we've already generated, so it might not be that big of a win.
    def generate(self):
        self.domains.append(
            {"fuzzer": "Original*", "domain-name": self.domain + "." + self.tld}
        )

        # XXX This could definitely be shortened up.
        for domain in self.__addition():
            self.domains.append(
                {"fuzzer": "Addition", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__bitsquatting():
            self.domains.append(
                {"fuzzer": "Bitsquatting", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__homoglyph():
            self.domains.append(
                {"fuzzer": "Homoglyph", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__hyphenation():
            self.domains.append(
                {"fuzzer": "Hyphenation", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__insertion():
            self.domains.append(
                {"fuzzer": "Insertion", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__omission():
            self.domains.append(
                {"fuzzer": "Omission", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__repetition():
            self.domains.append(
                {"fuzzer": "Repetition", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__replacement():
            self.domains.append(
                {"fuzzer": "Replacement", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__subdomain():
            self.domains.append(
                {"fuzzer": "Subdomain", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__transposition():
            self.domains.append(
                {"fuzzer": "Transposition", "domain-name": domain + "." + self.tld}
            )
        for domain in self.__vowel_swap():
            self.domains.append(
                {"fuzzer": "Vowel-swap", "domain-name": domain + "." + self.tld}
            )

        if "." in self.tld:
            self.domains.append(
                {
                    "fuzzer": "Various",
                    "domain-name": self.domain + "." + self.tld.split(".")[-1],
                }
            )
            self.domains.append(
                {"fuzzer": "Various", "domain-name": self.domain + self.tld}
            )
        if "." not in self.tld:
            self.domains.append(
                {
                    "fuzzer": "Various",
                    "domain-name": self.domain + self.tld + "." + self.tld,
                }
            )
        if self.tld != "com" and "." not in self.tld:
            self.domains.append(
                {
                    "fuzzer": "Various",
                    "domain-name": self.domain + "-" + self.tld + ".com",
                }
            )

        self.__filter_domains()
        return self.domains


class DomainDict(DomainGenerator):
    def __init__(self, domain):
        super().__init__(domain)
        self.dictionary = []

    def load_dict(self, path):
        path = pathlib.Path(path)
        words = set()
        try:
            with open(path, "rt") as fil:
                for word in fil:
                    word = word.strip()
                    if word.isalpha():
                        words.add(word)
        except OSError as err:
            p_err(f"Unable to open dictionary file {path}: {err}", 1)
        else:
            self.dictionary[:] = sorted(words)

    def __dictionary(self):
        result = collections.deque()

        domain = self.domain.rsplit(".", 1)
        if len(domain) > 1:
            prefix = domain[0] + "."
            name = domain[1]
        else:
            prefix = ""
            name = domain[0]

        for word in self.dictionary:
            result.append(prefix + name + "-" + word)
            result.append(prefix + name + word)
            result.append(prefix + word + "-" + name)
            result.append(prefix + word + name)

        return result

    def generate(self):
        for domain in self.__dictionary():
            self.domains.append(
                {"fuzzer": "Dictionary", "domain-name": domain + "." + self.tld}
            )
        return self.domains


class TldDict(DomainDict):
    def generate(self):
        if self.tld in self.dictionary:
            self.dictionary.remove(self.tld)
        for tld in self.dictionary:
            self.domains.append(
                {"fuzzer": "TLD-swap", "domain-name": self.domain + "." + tld}
            )
        return self.domains


# class DomainThread(threading.Thread):
#    def __init__(self, queue):
#        threading.Thread.__init__(self)
#        self.jobs = queue
#        self.kill_received = False
#
#        self.ssdeep_orig = ""
#        self.domain_orig = ""
#
#        self.uri_scheme = "http"
#        self.uri_path = ""
#        self.uri_query = ""
#
#        self.option_extdns = False
#        self.option_geoip = False
#        self.option_whois = False
#        self.option_ssdeep = False
#        self.option_banners = False
#        self.option_mxcheck = False
#
#    def __banner_http(self, ip, vhost):
#        try:
#            http = socket.socket()
#            http.settimeout(1)
#            http.connect((ip, 80))
#            http.send(
#                b"HEAD / HTTP/1.1\r\nHost: %s\r\nUser-agent: %s\r\n\r\n"
#                % (vhost.encode(), args.useragent.encode())
#            )
#            response = http.recv(1024).decode()
#            http.close()
#        except Exception:
#            pass
#        else:
#            sep = "\r\n" if "\r\n" in response else "\n"
#            headers = response.split(sep)
#            for field in headers:
#                if field.startswith("Server: "):
#                    return field[8:]
#            banner = headers[0].split(" ")
#            if len(banner) > 1:
#                return "HTTP %s" % banner[1]
#
#    def __banner_smtp(self, mx):
#        try:
#            smtp = socket.socket()
#            smtp.settimeout(1)
#            smtp.connect((mx, 25))
#            response = smtp.recv(1024).decode()
#            smtp.close()
#        except Exception:
#            pass
#        else:
#            sep = "\r\n" if "\r\n" in response else "\n"
#            hello = response.split(sep)[0]
#            if hello.startswith("220"):
#                return hello[4:].strip()
#            return hello[:40]
#
#    def __mxcheck(self, mx, from_domain, to_domain):
#        from_addr = "randombob" + str(randint(1, 9)) + "@" + from_domain
#        to_addr = "randomalice" + str(randint(1, 9)) + "@" + to_domain
#        try:
#            smtp = smtplib.SMTP(mx, 25, timeout=REQUEST_TIMEOUT_SMTP)
#            smtp.sendmail(from_addr, to_addr, "And that's how the cookie crumbles")
#            smtp.quit()
#        except Exception:
#            return False
#        else:
#            return True
#
#    def stop(self):
#        self.kill_received = True
#
#    @staticmethod
#    def answer_to_list(answers):
#        return sorted(
#            list(
#                map(
#                    lambda record: str(record).strip(".")
#                    if len(str(record).split(" ")) == 1
#                    else str(record).split(" ")[1].strip("."),
#                    answers,
#                )
#            )
#        )
#


def generate_idle(domains):
    output = ""

    for domain in domains:
        output += "%s\n" % idna.encode(domain.get("domain-name")).decode()

    return output


class DNSTwister:
    dictionary = None
    nameservers = ("8.8.8.8",)
    port = 53
    output_fmt = "cli"
    show_all = False
    worker_count = 10

    def __init__(
        self,
        domains,
        worker_count=worker_count,
        dictionary=dictionary,
        show_all=show_all,
        output_fmt=output_fmt,
        nameservers=nameservers,
        port=port,
    ):
        self.domains = domains
        self.worker_count = max(1, worker_count)
        self.show_all = show_all
        self.dictionary = dictionary
        self.output_fmt = output_fmt
        self.nameservers = asyncdns.RoundRobinServer(
            tuple((nameserver, port) for nameserver in nameservers)
        )

    def generate_cli(self, domains):
        output = ""

        width_fuzzer = max([len(d["fuzzer"]) for d in domains]) + 1
        width_domain = max([len(d["domain-name"]) for d in domains]) + 1

        for domain in domains:
            info = ""

            if "dns-a" in domain:
                info += self.one_or_all(domain["dns-a"])
                if "geoip-country" in domain:
                    info += FG_CYA + "/" + domain["geoip-country"] + FG_RST
                info += " "

            if "dns-aaaa" in domain:
                info += self.one_or_all(domain["dns-aaaa"]) + " "

            if "dns-ns" in domain:
                info += "%sNS:%s%s%s " % (
                    FG_YEL,
                    FG_CYA,
                    self.one_or_all(domain["dns-ns"]),
                    FG_RST,
                )

            if "dns-mx" in domain:
                if "mx-spy" in domain:
                    info += "%sSPYING-MX:%s%s" % (FG_YEL, domain["dns-mx"][0], FG_RST)
                else:
                    info += "%sMX:%s%s%s " % (
                        FG_YEL,
                        FG_CYA,
                        self.one_or_all(domain["dns-mx"]),
                        FG_RST,
                    )

            if "banner-http" in domain:
                info += '%sHTTP:%s"%s"%s ' % (
                    FG_YEL,
                    FG_CYA,
                    domain["banner-http"],
                    FG_RST,
                )

            if "banner-smtp" in domain:
                info += '%sSMTP:%s"%s"%s ' % (
                    FG_YEL,
                    FG_CYA,
                    domain["banner-smtp"],
                    FG_RST,
                )

            if "whois-created" in domain and "whois-updated" in domain:
                if domain["whois-created"] == domain["whois-updated"]:
                    info += "%sCreated/Updated:%s%s%s " % (
                        FG_YEL,
                        FG_CYA,
                        domain["whois-created"],
                        FG_RST,
                    )
                else:
                    if "whois-created" in domain:
                        info += "%sCreated:%s%s%s " % (
                            FG_YEL,
                            FG_CYA,
                            domain["whois-created"],
                            FG_RST,
                        )
                    if "whois-updated" in domain:
                        info += "%sUpdated:%s%s%s " % (
                            FG_YEL,
                            FG_CYA,
                            domain["whois-updated"],
                            FG_RST,
                        )

            if "ssdeep-score" in domain:
                if domain["ssdeep-score"] > 0:
                    info += "%sSSDEEP:%d%%%s " % (
                        FG_YEL,
                        domain["ssdeep-score"],
                        FG_RST,
                    )

            info = info.strip()

            if not info:
                info = "-"

            output += "%s%s%s %s %s\n" % (
                FG_BLU,
                domain["fuzzer"].ljust(width_fuzzer),
                FG_RST,
                domain["domain-name"].ljust(width_domain),
                info,
            )

        return output

    def generate_csv(self, domains):
        output = "fuzzer,domain-name,dns-a,dns-aaaa,dns-mx,dns-ns,geoip-country,whois-created,whois-updated,ssdeep-score\n"

        for domain in domains:
            output += "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n" % (
                domain.get("fuzzer"),
                idna.encode(domain.get("domain-name")).decode(),
                self.one_or_all(domain.get("dns-a", [""])),
                self.one_or_all(domain.get("dns-aaaa", [""])),
                self.one_or_all(domain.get("dns-mx", [""])),
                self.one_or_all(domain.get("dns-ns", [""])),
                domain.get("geoip-country", ""),
                domain.get("whois-created", ""),
                domain.get("whois-updated", ""),
                str(domain.get("ssdeep-score", "")),
            )

        return output

    @staticmethod
    def generate_json(domains):
        for domain in domains:
            domain["domain-name"] = idna.encode(domain["domain-name"].lower()).decode()
            domain["fuzzer"] = domain["fuzzer"].lower()

        return json.dumps(domains, indent=4, sort_keys=True)

    def one_or_all(self, answers):
        if self.show_all:
            result = ";".join(map(str, answers))
        else:
            if len(answers):
                result = str(answers[0])
            else:
                result = ""
        return result

    async def run(self):
        print(f"Processing {len(self.domains)} domain variants")
        tasks = []
        successes = collections.deque()

        total_domains = len(self.domains)
        progress_task = asyncio.create_task(self.status(total_domains, successes))
        resolver = asyncdns.Resolver()
        for i in range(self.worker_count):

            # worker.uri_scheme = url.scheme
            # worker.uri_path = url.path
            # worker.uri_query = url.query

            # worker.domain_orig = url.domain

            # if MODULE_DNSPYTHON:
            #    worker.option_extdns = True
            # if MODULE_WHOIS and args.whois:
            #    worker.option_whois = True
            # if MODULE_GEOIP and DB_GEOIP and args.geoip:
            #    worker.option_geoip = True
            # if args.banners:
            #    worker.option_banners = True
            # if (
            #    args.ssdeep
            #    and MODULE_REQUESTS
            #    and MODULE_SSDEEP
            #    and "ssdeep_orig" in locals()
            # ):
            #    worker.option_ssdeep = True
            #    worker.ssdeep_orig = ssdeep_orig
            # if args.mxcheck:
            #    worker.option_mxcheck = True

            # worker.start()
            tasks.append(self.start_worker(resolver, successes))

        await asyncio.gather(*tasks)
        progress_task.cancel()
        await progress_task

        if self.output_fmt == "csv":
            print(self.generate_csv(successes))
        elif self.output_fmt == "json":
            print(self.generate_json(successes))
        else:
            print(self.generate_cli(successes))

    async def start_worker(self, resolver, successes):
        while self.domains:
            domain = self.domains.popleft()
            query = asyncdns.Query(domain["domain-name"], asyncdns.A, asyncdns.IN)
            reply = await resolver.lookup(
                query, servers=self.nameservers, should_cache=False
            )

            if reply.rcode == asyncdns.NXDOMAIN:
                continue

            for answer in reply.answers:
                if isinstance(answer, (asyncdns.rr.A, asyncdns.rr.AAAA)):
                    domain.setdefault("dns-a", []).append(answer.address)
                elif isinstance(answer, asyncdns.rr.NS):
                    domain.setdefault("dns-ns", []).append(answer.unicode_host)
                elif isinstance(answer, asyncdns.rr.MX):
                    domain.setdefault("dns-mx", []).append(answer.unicode_exchange)
            successes.append(domain)

        #            if self.option_mxcheck:
        #                if "dns-mx" in domain:
        #                    if domain["domain-name"] is not self.domain_orig:
        #                        if self.__mxcheck(
        #                            domain["dns-mx"][0], self.domain_orig, domain["domain-name"]
        #                        ):
        #                            domain["mx-spy"] = True

    #
    #            if self.option_whois:
    #                if nxdomain is False and "dns-ns" in domain:
    #                    try:
    #                        whoisdb = whois.query(domain["domain-name"])
    #                        domain["whois-created"] = str(whoisdb.creation_date).split(" ")[
    #                            0
    #                        ]
    #                        domain["whois-updated"] = str(whoisdb.last_updated).split(" ")[
    #                            0
    #                        ]
    #                    except Exception:
    #                        pass
    #
    #            if self.option_geoip:
    #                if "dns-a" in domain:
    #                    gi = GeoIP.open(
    #                        FILE_GEOIP, GeoIP.GEOIP_INDEX_CACHE | GeoIP.GEOIP_CHECK_CACHE
    #                    )
    #                    try:
    #                        country = gi.country_name_by_addr(domain["dns-a"][0])
    #                    except Exception:
    #                        pass
    #                    else:
    #                        if country:
    #                            domain["geoip-country"] = country.split(",")[0]
    #
    #            if self.option_banners:
    #                if "dns-a" in domain:
    #                    banner = self.__banner_http(
    #                        domain["dns-a"][0], domain["domain-name"]
    #                    )
    #                    if banner:
    #                        domain["banner-http"] = banner
    #                if "dns-mx" in domain:
    #                    banner = self.__banner_smtp(domain["dns-mx"][0])
    #                    if banner:
    #                        domain["banner-smtp"] = banner
    #
    #            if self.option_ssdeep:
    #                if "dns-a" in domain:
    #                    try:
    #                        req = requests.get(
    #                            self.uri_scheme
    #                            + "://"
    #                            + domain["domain-name"]
    #                            + self.uri_path
    #                            + self.uri_query,
    #                            timeout=REQUEST_TIMEOUT_HTTP,
    #                            headers={"User-Agent": args.useragent},
    #                            verify=False,
    #                        )
    #                        # ssdeep_fuzz = ssdeep.hash(req.text.replace(' ', '').replace('\n', ''))
    #                        ssdeep_fuzz = ssdeep.hash(req.text)
    #                    except Exception:
    #                        pass
    #                    else:
    #                        if req.status_code // 100 == 2:
    #                            domain["ssdeep-score"] = ssdeep.compare(
    #                                self.ssdeep_orig, ssdeep_fuzz
    #                            )
    #
    #

    async def status(self, total_domains, successes):
        def msg():
            hits_percent = 100 * len(successes) / total_domains
            return (
                f"\r{len(self.domains)} remaining. "
                f"{len(successes)} hits ({hits_percent:.02f}%)\x1b[K"
            )

        start_time = datetime.datetime.now()
        while True:
            try:
                await asyncio.sleep(.5)
                print(msg(), end="")
            except asyncio.CancelledError:
                print(msg(), end="")
                break
        print()
        end_time = datetime.datetime.now()
        print(f"Took {end_time - start_time} to complete.")

        return


def main():
    # signal.signal(signal.SIGINT, sigint_handler)

    parser = argparse.ArgumentParser(
        usage="%s [OPTION]... DOMAIN" % sys.argv[0],
        add_help=True,
        description="""Find similar-looking domain names that adversaries can use to attack you. """
        """Can detect typosquatters, phishing attacks, fraud and corporate espionage. """
        """Useful as an additional source of targeted threat intelligence.""",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=30),
    )

    parser.add_argument("domain", help="domain name or URL to check")
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        dest="show_all",
        default=DNSTwister.show_all,
        help="show all DNS records",
    )
    #    parser.add_argument(
    #        "-b",
    #        "--banners",
    #        action="store_true",
    #        help="determine HTTP and SMTP service banners",
    #    )
    parser.add_argument(
        "-d",
        "--dictionary",
        type=str,
        default=DNSTwister.dictionary,
        metavar="FILE",
        help="generate additional domains using dictionary FILE",
    )
    #    parser.add_argument(
    #        "-g", "--geoip", action="store_true", help="perform lookup for GeoIP location"
    #    )
    #    parser.add_argument(
    #        "-m",
    #        "--mxcheck",
    #        action="store_true",
    #        help="check if MX host can be used to intercept e-mails",
    #    )
    parser.add_argument(
        "-f",
        "--format",
        type=str,
        choices=["cli", "csv", "json", "idle"],
        dest="output_fmt",
        default=DNSTwister.output_fmt,
        help="output format (default: cli)",
    )
    #    parser.add_argument(
    #        "-r",
    #        "--registered",
    #        action="store_true",
    #        help="show only registered domain names",
    #    )
    #    parser.add_argument(
    #        "-s",
    #        "--ssdeep",
    #        action="store_true",
    #        help="fetch web pages and compare their fuzzy hashes to evaluate similarity",
    #    )
    parser.add_argument(
        "-k",
        "--workers",
        dest="worker_count",
        type=int,
        metavar="NUMBER",
        default=DNSTwister.worker_count,
        help="start specified NUMBER of workers (default: %default)",
    )
    #    parser.add_argument(
    #        "-w",
    #        "--whois",
    #        action="store_true",
    #        help="perform lookup for WHOIS creation/update time (slow)",
    #    )
    #    parser.add_argument(
    #        "--tld",
    #        type=str,
    #        metavar="FILE",
    #        help="generate additional domains by swapping TLD from FILE",
    #    )
    parser.add_argument(
        "--nameservers",
        default=",".join(DNSTwister.nameservers),
        type=str,
        metavar="LIST",
        help="comma separated list of DNS servers to query",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DNSTwister.port,
        metavar="PORT",
        help="the port number to send queries to",
    )
    #    parser.add_argument(
    #        "--useragent",
    #        type=str,
    #        metavar="STRING",
    #        default="Mozilla/5.0 dnstwist/%s" % __version__,
    #        help="user-agent STRING to send with HTTP requests (default: Mozilla/5.0 dnstwist/%s)"
    #        % __version__,
    #    )

    #    if len(sys.argv) < 2:
    #        sys.stdout.write(
    #            "%sdnstwist %s by <%s>%s\n\n" % (ST_BRI, __version__, __email__, ST_RST)
    #        )
    #        parser.print_help()
    #        bye(0)

    args = parser.parse_args()
    args.nameservers = args.nameservers.split(",")

    try:
        url = UrlParser(args.domain)
    except ValueError as err:
        p_err("error: %s\n" % err)
        bye(-1)

    dfuzz = DomainFuzz(url.domain)
    domains = dfuzz.generate()

    if args.dictionary:
        if not pathlib.Path(args.dictionary).exists():
            p_err("error: dictionary not found: %s\n" % args.dictionary)
            bye(-1)
        ddict = DomainDict(url.domain)
        ddict.load_dict(args.dictionary)
        domains.extend(ddict.generate())

    #    if args.tld:
    #        if not path.exists(args.tld):
    #            p_err("error: dictionary not found: %s\n" % args.tld)
    #            bye(-1)
    #        tlddict = TldDict(url.domain)
    #        tlddict.load_dict(args.tld)
    #        domains.extend(tlddict.generate())

    if args.output_fmt == "idle":
        sys.stdout.write(generate_idle(domains))
        bye(0)

    #    if not DB_GEOIP and args.geoip:
    #        p_err("error: missing GeoIP database file: %\n" % FILE_GEOIP)
    #        bye(-1)

    print(
        FG_RND
        + ST_BRI
        + r"""     _           _            _     _
  __| |_ __  ___| |___      _(_)___| |_
 / _` | '_ \/ __| __\ \ /\ / / / __| __|
| (_| | | | \__ \ |_ \ V  V /| \__ \ |_
 \__,_|_| |_|___/\__| \_/\_/ |_|___/\__| {%s}

"""
        % __version__
        + FG_RST
        + ST_RST
    )

    #    if MODULE_WHOIS and args.whois:
    #        p_cli(
    #            "Disabling multithreaded job distribution in order to query WHOIS servers\n"
    #        )
    #        args.threads = 1

    #    if args.ssdeep and MODULE_SSDEEP and MODULE_REQUESTS:
    #        p_cli("Fetching content from: " + url.get_full_uri() + " ... ")
    #        try:
    #            req = requests.get(
    #                url.get_full_uri(),
    #                timeout=REQUEST_TIMEOUT_HTTP,
    #                headers={"User-Agent": args.useragent},
    #            )
    #        except requests.exceptions.ConnectionError:
    #            p_cli("Connection error\n")
    #            args.ssdeep = False
    #            pass
    #        except requests.exceptions.HTTPError:
    #            p_cli("Invalid HTTP response\n")
    #            args.ssdeep = False
    #            pass
    #        except requests.exceptions.Timeout:
    #            p_cli("Timeout (%d seconds)\n" % REQUEST_TIMEOUT_HTTP)
    #            args.ssdeep = False
    #            pass
    #        except Exception:
    #            p_cli("Failed!\n")
    #            args.ssdeep = False
    #            pass
    #        else:
    #            p_cli(
    #                "%d %s (%.1f Kbytes)\n"
    #                % (req.status_code, req.reason, float(len(req.text)) / 1000)
    #            )
    #            if req.status_code / 100 == 2:
    #                # ssdeep_orig = ssdeep.hash(req.text.replace(' ', '').replace('\n', ''))
    #                ssdeep_orig = ssdeep.hash(req.text)
    #            else:
    #                args.ssdeep = False

    #    jobs = queue.Queue()

    #    global threads
    #    threads = []
    #
    #    for i in range(len(domains)):
    #        jobs.put(domains[i])

    twister = DNSTwister(
        domains,
        worker_count=args.worker_count,
        show_all=args.show_all,
        output_fmt=args.output_fmt,
        nameservers=args.nameservers,
        port=args.port,
    )
    asyncio.run(twister.run())


if __name__ == "__main__":
    main()
