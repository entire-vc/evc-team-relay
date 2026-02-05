<script lang="ts">
  import { Checkbox as CheckboxPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = CheckboxPrimitive.RootProps & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<CheckboxPrimitive.Root
  class={cn(
    'peer h-4 w-4 shrink-0 rounded-sm border border-primary shadow focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
    className
  )}
  {...restProps}
>
  {#snippet children({ checked })}
    {#if checked}
      <span class="flex items-center justify-center text-current">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          class="h-3 w-3"
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
      </span>
    {/if}
  {/snippet}
</CheckboxPrimitive.Root>
