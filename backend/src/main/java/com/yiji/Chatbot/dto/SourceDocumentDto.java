package com.yiji.Chatbot.dto;

import lombok.Builder;

/**
 * RAG 검색으로 찾은 Confluence 출처 문서 DTO
 *
 * @param category 대분류 카테고리 (예: 솔루션/개발, 피앤씨)
 * @param path     전체 계층 경로 (브레드크럼)
 * @param snippet  검색된 청크 본문 발췌
 * @param score    검색 유사도 결합 점수
 */
@Builder
public record SourceDocumentDto(
        String title,
        String url,
        String category,
        String path,
        String author,
        String snippet,
        Double score
) {
}
