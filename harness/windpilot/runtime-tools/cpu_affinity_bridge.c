#define _GNU_SOURCE
/* Resource allocation only: ordinary sched_setaffinity is emulated by the
 * owning host worker. Every other native syscall is allowed by this filter. */
#include <errno.h>
#include <fcntl.h>
#include <linux/audit.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <poll.h>

#if defined(__x86_64__)
#define NATIVE_ARCH AUDIT_ARCH_X86_64
#define COMPAT_ARCH AUDIT_ARCH_I386
#define ALTERNATE_AFFINITY_NR (__NR_sched_setaffinity | 0x40000000U)
#elif defined(__aarch64__)
#define NATIVE_ARCH AUDIT_ARCH_AARCH64
#define COMPAT_ARCH AUDIT_ARCH_ARM
#define ALTERNATE_AFFINITY_NR __NR_sched_setaffinity
#else
#error "CPU affinity bridge requires a qualified x86_64 or aarch64 native ABI"
#endif

static void fail(const char *what) { perror(what); _exit(125); }

int main(int argc, char **argv) {
    if (argc < 5 || strcmp(argv[1], "--listener-socket-fd") || strcmp(argv[3], "--")) {
        fprintf(stderr, "usage: %s --listener-socket-fd FD -- PROGRAM [ARG ...]\n", argv[0]);
        return 125;
    }
    char *end = NULL;
    long value = strtol(argv[2], &end, 10);
    if (!end || *end || value < 0 || value > INT32_MAX) return 125;
    int channel = (int)value;
    /* Account for native, x32 and 32-bit compatibility affinity syscalls. Every
     * other syscall is allowed; no command/library/network filter is installed. */
    struct sock_filter filter[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, NATIVE_ARCH, 2, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, COMPAT_ARCH, 6, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_sched_setaffinity, 1, 0),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, ALTERNATE_AFFINITY_NR, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, 241, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_USER_NOTIF),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog program = { .len = sizeof(filter) / sizeof(filter[0]), .filter = filter };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) fail("no_new_privs");
    int listener = syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER,
                           SECCOMP_FILTER_FLAG_NEW_LISTENER, &program);
    if (listener < 0) fail("seccomp affinity listener");
    char marker = 'A';
    struct iovec data = { .iov_base = &marker, .iov_len = 1 };
    union { struct cmsghdr align; char bytes[CMSG_SPACE(sizeof(int))]; } ancillary = {0};
    struct msghdr message = { .msg_iov = &data, .msg_iovlen = 1,
                             .msg_control = ancillary.bytes, .msg_controllen = sizeof(ancillary.bytes) };
    struct cmsghdr *control = CMSG_FIRSTHDR(&message);
    control->cmsg_level = SOL_SOCKET; control->cmsg_type = SCM_RIGHTS;
    control->cmsg_len = CMSG_LEN(sizeof(int));
    memcpy(CMSG_DATA(control), &listener, sizeof(listener));
    if (sendmsg(channel, &message, MSG_NOSIGNAL) != 1) fail("send affinity listener");
    close(listener);
    /* Do not exec until the parent is serving requests. No listener/socket FD
     * reaches bwrap or task code. A dead/misconfigured parent fails startup. */
    struct pollfd waiting = { .fd = channel, .events = POLLIN };
    int ready;
    do { ready = poll(&waiting, 1, 30000); } while (ready < 0 && errno == EINTR);
    if (ready != 1 || recv(channel, &marker, 1, 0) != 1 || marker != 'R') {
        fprintf(stderr, "affinity parent did not acknowledge startup\n"); return 125;
    }
    close(channel);
    execvp(argv[4], &argv[4]);
    fail("exec affinity target");
}
