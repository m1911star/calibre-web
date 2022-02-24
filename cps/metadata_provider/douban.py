# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#    Copyright (C) 2022 xlivevil
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.
import re
from concurrent import futures
from typing import List, Optional

import requests
from html2text import HTML2Text
from lxml import etree

from cps import logger
from cps.services.Metadata import Metadata, MetaRecord, MetaSourceInfo

log = logger.create()


def html2text(html: str) -> str:

    h2t = HTML2Text()
    h2t.body_width = 0
    h2t.single_line_break = True
    h2t.emphasis_mark = "*"
    return h2t.handle(html)


class Douban(Metadata):
    __name__ = "豆瓣"
    __id__ = "douban"
    DESCRIPTION = "豆瓣"
    META_URL = "https://book.douban.com/"
    SEARCH_URL = "https://www.douban.com/j/search"

    ID_PATTERN = re.compile(r"sid: (?P<id>\d+),")
    AUTHORS_PATTERN = re.compile(r"作者|译者")
    PUBLISHER_PATTERN = re.compile(r"出版社")
    SUBTITLE_PATTERN = re.compile(r"副标题")
    PUBLISHED_DATE_PATTERN = re.compile(r"出版年")
    SERIES_PATTERN = re.compile(r"丛书")
    IDENTIFIERS_PATTERN = re.compile(r"ISBN|统一书号")

    TITTLE_XPATH = "//span[@property='v:itemreviewed']"
    COVER_XPATH = "//a[@class='nbg']"
    INFO_XPATH = "//*[@id='info']//span[@class='pl']"
    TAGS_XPATH = "//a[contains(@class, 'tag')]"
    DESCRIPTION_XPATH = "//div[@id='link-report']//div[@class='intro']"
    RATING_XPATH = "//div[@class='rating_self clearfix']/strong"

    session = requests.Session()
    session.headers = {
        'user-agent':
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.102 Safari/537.36 Edg/98.0.1108.56',
    }

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if self.active:
            log.debug(f"starting search {query} on douban")
            if title_tokens := list(
                self.get_title_tokens(query, strip_joiners=False)
            ):
                query = "+".join(title_tokens)

            try:
                r = self.session.get(
                    self.SEARCH_URL, params={"cat": 1001, "q": query}
                )
                r.raise_for_status()

            except Exception as e:
                log.warning(e)
                return None

            results = r.json()
            if results["total"] == 0:
                return val

            book_id_list = [
                self.ID_PATTERN.search(item).group("id")
                for item in results["items"][:10] if self.ID_PATTERN.search(item)
            ]

            with futures.ThreadPoolExecutor(max_workers=5) as executor:

                fut = [
                    executor.submit(self._parse_single_book, book_id, generic_cover)
                    for book_id in book_id_list
                ]
                
                val = [
                    future.result() 
                    for future in futures.as_completed(fut) if future.result()
                ]

        return val

    def _parse_single_book(
        self, id: str, generic_cover: str = ""
    ) -> Optional[MetaRecord]:
        url = f"https://book.douban.com/subject/{id}/"

        try:
            r = self.session.get(url)
            r.raise_for_status()
        except Exception as e:
            log.warning(e)
            return None

        match = MetaRecord(
            id=id,
            title="",
            authors=[],
            url=url,
            source=MetaSourceInfo(
                id=self.__id__,
                description=self.DESCRIPTION,
                link=self.META_URL,
            ),
        )

        html = etree.HTML(r.content.decode("utf8"))

        match.title = html.xpath(self.TITTLE_XPATH)[0].text
        match.cover = html.xpath(self.COVER_XPATH)[0].attrib["href"] or generic_cover
        try:
            rating_num = float(html.xpath(self.RATING_XPATH)[0].text.strip())
        except ValueError:
            rating_num = 0
        match.rating = int(-1 * rating_num // 2 * -1) if rating_num else 0

        tag_elements = html.xpath(self.TAGS_XPATH)
        if len(tag_elements):
            match.tags = [tag_element.text for tag_element in tag_elements]

        description_element = html.xpath(self.DESCRIPTION_XPATH)
        if len(description_element):
            match.description = html2text(etree.tostring(
                description_element[-1], encoding="utf8").decode("utf8"))

        info = html.xpath(self.INFO_XPATH)

        for element in info:
            text = element.text
            if self.AUTHORS_PATTERN.search(text):
                next = element.getnext()
                while next is not None and next.tag != "br":
                    match.authors.append(next.text)
                    next = next.getnext()
            elif self.PUBLISHER_PATTERN.search(text):
                match.publisher = element.tail.strip()
            elif self.SUBTITLE_PATTERN.search(text):
                match.title = f'{match.title}:' + element.tail.strip()
            elif self.PUBLISHED_DATE_PATTERN.search(text):
                match.publishedDate = element.tail.strip()
            elif self.SUBTITLE_PATTERN.search(text):
                match.series = element.getnext().text
            elif i_type := self.IDENTIFIERS_PATTERN.search(text):
                match.identifiers[i_type.group()] = element.tail.strip()

        return match
