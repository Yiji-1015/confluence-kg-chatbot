package com.yiji.Chatbot.entity;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.springframework.data.domain.Persistable;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 대화방(세션) 엔티티
 * - 대화방 ID (UUID), 제목, 생성/수정 시각을 관리하며 RDB에 영구 보관됩니다.
 * - 사용자가 과거 대화 목록을 조회하거나 특정 대화방을 다시 열어볼 때 사용됩니다.
 */
@Entity
@Table(name = "chat_sessions")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatSession implements Persistable<String> {

    @Id
    @Column(name = "session_id", length = 64)
    private String id;

    @Column(name = "user_id", length = 64)
    private String userId;

    @Column(name = "title", length = 200, nullable = false)
    private String title;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @OneToMany(mappedBy = "session", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<ChatMessage> messages = new ArrayList<>();

    /**
     * 새 엔티티인지 여부. DB에 저장되는 값이 아니다.
     *
     * `@Id`가 직접 할당된 String이고 `@Version`도 없어서, Spring Data는 `id != null`만 보고
     * "이미 존재하는 엔티티"라고 판단한다. 그래서 `save()`가 `persist`가 아니라 `merge`로 가고,
     * merge는 존재 확인을 위해 INSERT 전에 SELECT를 한 번 더 낸다.
     * Persistable을 구현해 그 판단을 우리가 직접 내린다.
     */
    @Transient
    private boolean isNew = true;

    @Builder
    public ChatSession(String id, String userId, String title) {
        this.id = id;
        this.userId = userId;
        this.title = title;
        this.createdAt = LocalDateTime.now();
        this.updatedAt = LocalDateTime.now();
    }

    @Override
    public boolean isNew() {
        return isNew;
    }

    /**
     * 영속화되었거나 DB에서 읽어온 시점부터는 새 엔티티가 아니다.
     */
    @PostPersist
    @PostLoad
    void markNotNew() {
        this.isNew = false;
    }

    /**
     * 마지막 대화 시각 갱신
     */
    public void updateTimestamp() {
        this.updatedAt = LocalDateTime.now();
    }

    /**
     * 대화방 제목 변경 (필요 시)
     */
    public void updateTitle(String newTitle) {
        this.title = newTitle;
        this.updatedAt = LocalDateTime.now();
    }
}
