<!-- src/components/ProfileDropdown.vue -->
<template>
  <div class="dropdown dropdown-end">
    <div
      tabindex="0"
      role="button"
      class="btn btn-ghost btn-circle"
    >
      <div class="avatar avatar-placeholder">
        <div class="bg-neutral text-neutral-content rounded-full">
          <span>{{ userInitials }}</span>
        </div>
      </div>
    </div>
    <ul
      tabindex="0"
      class="menu menu-sm dropdown-content shadow bg-base-100 rounded-box"
    >
      <li class="text-center">
        <div class="font-medium">
          {{ user.preferred_username }}
          <span class="text-xs block opacity-70">{{ user.email }}</span>
        </div>
      </li>
      <li v-if="user.roles && user.roles.length > 0">
        <div>
          <span
            v-for="role in user.roles"
            :key="role"
            class="badge badge-sm"
          >
            {{ role }}
          </span>
        </div>
      </li>
      <div class="divider" />
      <li>
        <router-link to="/tokens">
          {{ t('nav.apiTokens') }}
        </router-link>
      </li>
      <li><a @click="$emit('logout')">{{ t('nav.logout') }}</a></li>
    </ul>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  user: {
    type: Object,
    required: true
  }
})

defineEmits(['logout'])

const { t } = useI18n()

const userInitials = computed(() => {
  if (!props.user) return '?'
  
  if (props.user.name) {
    // Use the first letter of first and last name
    const nameParts = props.user.name.split(' ')
    if (nameParts.length > 1) {
      return `${nameParts[0][0]}${nameParts[nameParts.length - 1][0]}`.toUpperCase()
    }
    // Just use the first letter if only one name
    return props.user.name[0].toUpperCase()
  }
  
  // Fall back to the first letter of the username
  return props.user.preferred_username ? props.user.preferred_username[0].toUpperCase() : '?'
})
</script>