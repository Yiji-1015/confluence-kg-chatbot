package com.yiji.Chatbot.mapper;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yiji.Chatbot.dto.ChatMessageDto;
import com.yiji.Chatbot.dto.ChatSessionDto;
import com.yiji.Chatbot.dto.InternalChatDto;
import com.yiji.Chatbot.dto.SourceDocumentDto;
import com.yiji.Chatbot.entity.ChatMessage;
import com.yiji.Chatbot.entity.ChatSession;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Entity와 DTO 간의 변환을 담당하는 Mapper 컴포넌트
 */
@Component
@RequiredArgsConstructor
public class ChatMapper {

    private final ObjectMapper objectMapper;

    /**
     * ChatSession 엔티티 -> ChatSessionDto 변환 (대화방 목록용)
     */
    public ChatSessionDto toSessionDto(ChatSession session) {
        if (session == null) {
            return null;
        }
        return ChatSessionDto.builder()
                .id(session.getId())
                .userId(session.getUserId())
                .title(session.getTitle())
                .createdAt(session.getCreatedAt())
                .updatedAt(session.getUpdatedAt())
                .build();
    }

    /**
     * ChatMessage 엔티티 -> ChatMessageDto 변환 (대화 내역용)
     */
    public ChatMessageDto toMessageDto(ChatMessage message) {
        if (message == null) {
            return null;
        }

        List<SourceDocumentDto> sources = parseSourcesJson(message.getSourcesJson());

        return ChatMessageDto.builder()
                .id(message.getId())
                .role(message.getRole())
                .content(message.getContent())
                .sources(sources)
                .createdAt(message.getCreatedAt())
                .build();
    }

    /**
     * Python AI 서버 출처 목록 -> 프론트엔드 출처 DTO 목록 변환
     */
    public List<SourceDocumentDto> toSourceDtoList(List<InternalChatDto.SourceDocument> internalDocs) {
        if (internalDocs == null || internalDocs.isEmpty()) {
            return Collections.emptyList();
        }
        return internalDocs.stream()
                .map(doc -> SourceDocumentDto.builder()
                        .title(doc.getTitle())
                        .url(doc.getUrl())
                        .author(doc.getAuthor())
                        .category(doc.getCategory())
                        .score(doc.getScore())
                        .build())
                .collect(Collectors.toList());
    }

    /**
     * 출처 DTO 목록 -> DB 저장용 JSON 문자열 변환
     */
    public String sourcesToJson(List<SourceDocumentDto> sources) {
        if (sources == null || sources.isEmpty()) {
            return null;
        }
        try {
            return objectMapper.writeValueAsString(sources);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * DB JSON 문자열 -> 출처 DTO 목록 역직렬화
     */
    private List<SourceDocumentDto> parseSourcesJson(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            return objectMapper.readValue(json, new TypeReference<List<SourceDocumentDto>>() {});
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }
}
