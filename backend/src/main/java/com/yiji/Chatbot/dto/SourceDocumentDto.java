package com.yiji.Chatbot.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * RAG 검색으로 찾은 Confluence 출처 문서 DTO
 */
@Getter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class SourceDocumentDto {

    private String title;      // 문서 제목
    private String url;        // Confluence 바로가기 링크
    private String category;   // 대분류 카테고리 (예: 솔루션/개발, 피앤씨)
    private String path;       // 전체 계층 경로 (브레드크럼)
    private String author;     // 작성자
    private String snippet;    // 검색된 청크 본문 발췌
    private Double score;      // 검색 유사도 결합 점수
}
