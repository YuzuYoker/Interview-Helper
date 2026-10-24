<script setup>
defineProps({
  size: { type: Number, default: 28 },
})
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <rect width="48" height="48" rx="13" fill="#4d6bfe" />
    <path
      d="M15 16.6C15 14.61 16.61 13 18.6 13h10.8c1.99 0 3.6 1.61 3.6 3.6v7.1c0 1.99-1.61 3.6-3.6 3.6h-6.3l-5.14 3.5c-.66.45-1.56-.02-1.56-.82V16.6Z"
      fill="#fff"
      fill-opacity=".97"
    />
    <path
      d="M20.2 19.6h7.6M20.2 23.4h5.1"
      stroke="#4d6bfe"
      stroke-width="2.2"
      stroke-linecap="round"
    />
  </svg>
</template>
