package com.yiji.Chatbot.entity;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 대화 메시지 엔티티
 * - 대화방 내에서 오고 간 질문(USER)과 답변(ASSISTANT), RAG 출처 정보를 영구 보관합니다.
 */
@Entity
@Table(name = "chat_messages")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "message_id")
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "session_id", nullable = false)
    private ChatSession session;

    @Column(name = "role", length = 20, nullable = false)
    private String role; // "USER" 또는 "ASSISTANT"

    @Column(name = "content", columnDefinition = "TEXT", nullable = false)
    private String content;

    @Column(name = "sources_json", columnDefinition = "TEXT")
    private String sourcesJson; // RAG 참조 문서 목록 (JSON 문자열)

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Builder
    public ChatMessage(ChatSession session, String role, String content, String sourcesJson) {
        this.session = session;
        this.role = role;
        this.content = content;
        this.sourcesJson = sourcesJson;
        this.createdAt = LocalDateTime.now();
    }
}
