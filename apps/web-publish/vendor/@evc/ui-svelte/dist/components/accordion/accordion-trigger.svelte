<script lang="ts">
  import { Accordion as AccordionPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = AccordionPrimitive.TriggerProps & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<AccordionPrimitive.Header class="flex">
  <AccordionPrimitive.Trigger
    class={cn(
      'flex flex-1 items-center justify-between py-4 font-medium transition-all hover:underline [&[data-state=open]>svg]:rotate-180',
      className
    )}
    {...restProps}
  >
    {@render children?.()}
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
      class="h-4 w-4 shrink-0 transition-transform duration-200"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  </AccordionPrimitive.Trigger>
</AccordionPrimitive.Header>
