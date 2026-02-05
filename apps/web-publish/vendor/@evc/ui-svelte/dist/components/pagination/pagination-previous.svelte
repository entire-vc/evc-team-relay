<script lang="ts">
  import { cn } from '../../utils.js';
  import PaginationLink from './pagination-link.svelte';
  import type { HTMLAnchorAttributes } from 'svelte/elements';

  type Props = HTMLAnchorAttributes & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<PaginationLink
  aria-label="Go to previous page"
  size="default"
  class={cn('gap-1 pl-2.5', className)}
  {...restProps}
>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="24"
    height="24"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    class="h-4 w-4"
  >
    <path d="m15 18-6-6 6-6" />
  </svg>
  <span>Previous</span>
</PaginationLink>
